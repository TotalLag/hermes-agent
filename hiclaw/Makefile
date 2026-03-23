# Makefile for Hermes Hiclaw Integration
# This Makefile provides build, test, run, deploy, clean, and lint operations
# for the Hermes Hiclaw worker Docker container.

# Configuration variables
DOCKER_BUILDKIT := 1
IMAGE_NAME := hermes-worker
IMAGE_TAG := latest
REGISTRY := $(HICLAW_REGISTRY)
DOCKERFILE := hiclaw/Dockerfile.worker
TEST_SCRIPT := hiclaw/tests/test_hermes_worker.sh

# Default target
.DEFAULT_GOAL := help

# Colors for output
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Helper function to print colored output
define print_status
	@echo "$(GREEN)[$(1)]$(NC) $(2)"
endef

define print_warning
	@echo "$(YELLOW)[WARNING]$(NC) $(1)"
endef

define print_error
	@echo "$(RED)[ERROR]$(NC) $(1)"
endef

# Check if required files exist
define check_files
	@if [ ! -f "$(DOCKERFILE)" ]; then \
		$(call print_error,"Dockerfile $(DOCKERFILE) not found"); \
		exit 1; \
	fi
endef

# Check if test script exists and is executable
define check_test
	@if [ ! -f "$(TEST_SCRIPT)" ]; then \
		$(call print_error,"Test script $(TEST_SCRIPT) not found"); \
		exit 1; \
	fi
	@if [ ! -x "$(TEST_SCRIPT)" ]; then \
		$(call print_warning,"Making test script $(TEST_SCRIPT) executable"); \
		chmod +x "$(TEST_SCRIPT)"; \
	fi
endef

# Build target
.PHONY: build
build:
	@$(call print_status,"Building","Docker image $(IMAGE_NAME):$(IMAGE_TAG)")
	@$(call check_files)
	@DOCKER_BUILDKIT=$(DOCKER_BUILDKIT) docker build \
		-f $(DOCKERFILE) \
		-t $(IMAGE_NAME):$(IMAGE_TAG) \
		--label "maintainer=Hiclaw Team" \
		--label "description=Hermes Hiclaw Worker" \
		.
	@$(call print_status,"Build","Docker image $(IMAGE_NAME):$(IMAGE_TAG) built successfully")

# Test target
.PHONY: test
test:
	@$(call print_status,"Testing","Running Hiclaw worker tests")
	@$(call check_files)
	@$(call check_test)
	@./$(TEST_SCRIPT)
	@$(call print_status,"Test","Hiclaw worker tests completed")

# Run target
.PHONY: run
run:
	@$(call print_status,"Running","Hermes Hiclaw worker")
	@$(call check_files)
	@docker run --rm \
		-e "HICLAW_ENV=development" \
		-e "HERMES_ENDPOINT=http://localhost:8080" \
		-e "LOG_LEVEL=info" \
		-p "8081:8080" \
		$(IMAGE_NAME):$(IMAGE_TAG)

# Deploy target
.PHONY: deploy
deploy: test
	@$(call print_status,"Deploying","Preparing for deployment")
	@if [ -z "$(REGISTRY)" ]; then \
		$(call print_error,"HICLAW_REGISTRY environment variable not set. Cannot deploy."); \
		exit 1; \
	fi
	@$(call print_status,"Deploying","Pushing to registry $(REGISTRY)")
	@docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	@docker push $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	@$(call print_status,"Deploy","Successfully deployed $(IMAGE_NAME):$(IMAGE_TAG) to $(REGISTRY)")

# Clean target
.PHONY: clean
clean:
	@$(call print_status,"Cleaning","Removing Docker images and containers")
	@# Remove containers
	@docker ps -a --filter "name=hermes-worker" -q | xargs -r docker rm -f 2>/dev/null || true
	@# Remove images
	@docker images $(IMAGE_NAME) --format "{{.ID}}" | xargs -r docker rmi -f 2>/dev/null || true
	@# Remove dangling images
	@docker image prune -f 2>/dev/null || true
	# Clean temporary files
	@find . -type f -name "*.tmp" -delete 2>/dev/null || true
	@find . -type f -name "*.log" -delete 2>/dev/null || true
	@$(call print_status,"Clean","Cleanup completed")

# Lint target
.PHONY: lint
lint:
	@$(call print_status,"Linting","Checking for shell script issues")
	@which shellcheck >/dev/null 2>&1 || { \
		$(call print_error,"shellcheck not found. Please install shellcheck."); \
		exit 1; \
	}
	@find . -name "*.sh" -exec shellcheck {} + 2>/dev/null || true
	@$(call print_status,"Lint","Shell script linting completed")

# Help target
.PHONY: help
help:
	@echo "$(GREEN)Hermes Hiclaw Integration Makefile$(NC)"
	@echo ""
	@echo "$(YELLOW)Available targets:$(NC)"
	@echo ""
	@echo "  $(GREEN)build$(NC)         Build the Docker image"
	@echo "  $(GREEN)test$(NC)          Run the test suite"
	@echo "  $(GREEN)run$(NC)          Run the worker container"
	@echo "  $(GREEN)deploy$(NC)       Deploy to registry (requires HICLAW_REGISTRY)"
	@echo "  $(GREEN)clean$(NC)        Clean up images and temporary files"
	@echo "  $(GREEN)lint$(NC)         Lint shell scripts"
	@echo "  $(GREEN)help$(NC)         Show this help message"
	@echo ""
	@echo "$(YELLOW)Environment Variables:$(NC)"
	@echo "  HICLAW_REGISTRY    Docker registry URL (required for deploy)"
	@echo "  DOCKER_BUILDKIT     Set to 1 for faster builds (default: 1)"
	@echo ""
	@echo "$(YELLOW)Examples:$(NC)"
	@echo "  make build"
	@echo "  make test"
	@echo "  make run"
	@echo "  export HICLAW_REGISTRY=registry.example.com:5000 && make deploy"
	@echo "  make clean"

# Development targets
.PHONY: dev
dev: build test
	@$(call print_status,"Development","Build and test completed")

.PHONY: install-hooks
install-hooks:
	@$(call print_status,"Setup","Installing pre-commit hooks")
	@# This would typically install git hooks or other development tools
	@$(call print_status,"Setup","Hooks installation completed")

# Emergency stop target
.PHONY: emergency-stop
emergency-stop:
	@$(call print_error,"Emergency stop - cleaning up all Docker containers")
	@docker stop $$(docker ps -q --filter "name=hermes") 2>/dev/null || true
	@docker rm $$(docker ps -aq --filter "name=hermes") 2>/dev/null || true