#!/bin/bash
# =============================================================================
# test_hermes_worker.sh - Integration Tests for Hermes hiclaw Worker
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PASS=0
FAIL=0

log_test() {
    echo "[TEST] $*"
}

pass() {
    echo "  PASS: $*"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL: $*"
    FAIL=$((FAIL + 1))
}

assert_file_exists() {
    local file="$1"
    if [[ -f "${file}" ]]; then
        pass "File exists: ${file}"
        return 0
    else
        fail "File missing: ${file}"
        return 1
    fi
}

assert_contains() {
    local file="$1"
    local pattern="$2"
    if grep -q "${pattern}" "${file}" 2>/dev/null; then
        pass "Contains '${pattern}': ${file}"
        return 0
    else
        fail "Missing '${pattern}': ${file}"
        return 1
    fi
}

assert_not_contains() {
    local file="$1"
    local pattern="$2"
    if ! grep -q "${pattern}" "${file}" 2>/dev/null; then
        pass "Does not contain '${pattern}': ${file}"
        return 0
    else
        fail "Contains '${pattern}' (should not): ${file}"
        return 1
    fi
}

setup_mocks() {
    MOCK_BIN="${ROOT_DIR}/.test_mock_bin"
    mkdir -p "${MOCK_BIN}"
    
    cat > "${MOCK_BIN}/mc" <<'MOCK_MC'
#!/bin/bash
echo "MC: $*" >> "${MC_LOG:-/tmp/mc.log}"
if [[ "$*" == *"ls"* ]] || [[ "$*" == *"stat"* ]]; then
    echo "{}"
fi
exit 0
MOCK_MC
    chmod +x "${MOCK_BIN}/mc"
    
    cat > "${MOCK_BIN}/curl" <<'MOCK_CURL'
#!/bin/bash
echo "CURL: $*" >> "${CURL_LOG:-/tmp/curl.log}"
echo '{"status":"ok"}'
exit 0
MOCK_CURL
    chmod +x "${MOCK_BIN}/curl"
    
    export PATH="${MOCK_BIN}:${PATH}"
}

cleanup_mocks() {
    rm -rf "${MOCK_BIN:-${ROOT_DIR}/.test_mock_bin}"
}

cleanup() {
    cleanup_mocks
    rm -f /tmp/mc.log /tmp/curl.log /tmp/transform_test.json /tmp/transform_test.yaml
}

trap cleanup EXIT

test_dockerfile_worker() {
    log_test "Testing Dockerfile.worker..."
    
    assert_file_exists "${ROOT_DIR}/Dockerfile.worker" || return 1
    assert_contains "${ROOT_DIR}/Dockerfile.worker" "FROM python:3.11-slim"
    assert_contains "${ROOT_DIR}/Dockerfile.worker" "ENTRYPOINT"
    assert_not_contains "${ROOT_DIR}/Dockerfile.worker" "ENV HICLAW_SECRET"
}

test_config_transform_script() {
    log_test "Testing hiclaw_config_transform.py..."
    
    assert_file_exists "${ROOT_DIR}/scripts/hiclaw_config_transform.py" || return 1
    
    cat > /tmp/transform_test.json <<'TEST_JSON'
{
    "model": "openrouter/anthropic/claude-3-5-sonnet",
    "provider": "openrouter",
    "channels": {
        "cli": {"type": "cli"}
    },
    "toolsets": ["files", "bash"],
    "skills": ["code-review"],
    "memory": {"enabled": true}
}
TEST_JSON
    
    if command -v python3 &>/dev/null; then
        local python_cmd="python3"
    elif command -v python &>/dev/null; then
        local python_cmd="python"
    else
        fail "Python not available for transform test"
        return 1
    fi
    
    if ${python_cmd} "${ROOT_DIR}/scripts/hiclaw_config_transform.py" /tmp/transform_test.json /tmp/transform_test.yaml 2>/dev/null; then
        if [[ -f /tmp/transform_test.yaml ]]; then
            pass "Config transform ran successfully"
            grep -q "default:" /tmp/transform_test.yaml && pass "Model section generated"
            grep -q "channels:" /tmp/transform_test.yaml && pass "Channels section generated"
        else
            fail "Output YAML not created"
        fi
    else
        fail "Config transform script failed"
    fi
}

test_sync_script() {
    log_test "Testing hiclaw-sync.sh..."
    
    assert_file_exists "${ROOT_DIR}/scripts/hiclaw-sync.sh" || return 1
    
    export HICLAW_MC_HOST="http://localhost:9000"
    export HICLAW_BUCKET="test-bucket"
    export HICLAW_ACCESS_KEY="testkey"
    export HICLAW_SECRET_KEY="testsecret"
    
    setup_mocks
    touch /tmp/mc.log
    
    if bash "${ROOT_DIR}/scripts/hiclaw-sync.sh" pull openclaw.json /tmp/ 2>/dev/null; then
        pass "hiclaw-sync.sh pull command executes"
    else
        fail "hiclaw-sync.sh pull command failed"
    fi
    
    cleanup_mocks
}

test_worker_registration_script() {
    log_test "Testing create-hermes-worker.sh..."
    
    assert_file_exists "${ROOT_DIR}/scripts/create-hermes-worker.sh" || return 1
    
    export HICLAW_MATRIX_HOMESERVER="https://matrix.example.com"
    export HICLAW_MATRIX_ACCESS_TOKEN="test_token"
    export HICLAW_MATRIX_USER_ID="@test:matrix.example.com"
    export HICLAW_MATRIX_DEVICE_ID="TEST01"
    export HICLAW_MANAGER_ROOM_ID="!test:matrix.example.com"
    export HICLAW_WORKER_NAME="test-worker"
    
    setup_mocks
    touch /tmp/curl.log
    
    if bash "${ROOT_DIR}/scripts/create-hermes-worker.sh" register 2>/dev/null; then
        pass "create-hermes-worker.sh register executes"
    else
        fail "create-hermes-worker.sh register failed"
    fi
    
    cleanup_mocks
}

test_entrypt_script() {
    log_test "Testing hermes-entrypoint.sh..."
    
    assert_file_exists "${ROOT_DIR}/scripts/hermes-entrypoint.sh" || return 1
    assert_contains "${ROOT_DIR}/scripts/hermes-entrypoint.sh" "pull_config"
    assert_contains "${ROOT_DIR}/scripts/hermes-entrypoint.sh" "transform_config"
    assert_contains "${ROOT_DIR}/scripts/hermes-entrypoint.sh" "launch_hermes"
}

test_makefile() {
    log_test "Testing Makefile..."
    
    assert_file_exists "${ROOT_DIR}/Makefile" || return 1
    assert_contains "${ROOT_DIR}/Makefile" "build:"
    assert_contains "${ROOT_DIR}/Makefile" "test:"
    assert_contains "${ROOT_DIR}/Makefile" "clean:"
}

main() {
    echo "=========================================="
    echo "Hermes hiclaw Worker Integration Tests"
    echo "=========================================="
    echo ""
    
    test_dockerfile_worker
    test_config_transform_script
    test_sync_script
    test_worker_registration_script
    test_entrypt_script
    test_makefile
    
    echo ""
    echo "=========================================="
    echo "Results: ${PASS} passed, ${FAIL} failed"
    echo "=========================================="
    
    if [[ ${FAIL} -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
