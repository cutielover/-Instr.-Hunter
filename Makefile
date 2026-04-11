# RISC-V / AArch64 Sifter Makefile

CC = gcc
CFLAGS = -O2 -Wall -Wextra -g -fno-stack-protector
LDFLAGS = -lpthread

# AArch64 cross (Linux GNU target); override on native arm64 host: make CC_AARCH64=gcc
CC_AARCH64 ?= aarch64-linux-gnu-gcc

USE_CAPSTONE ?= 1
ifeq ($(USE_CAPSTONE),1)
    CFLAGS += -DUSE_CAPSTONE
    LDFLAGS += -lcapstone
endif

CROSS_COMPILE ?=
ifneq ($(CROSS_COMPILE),)
    CC = $(CROSS_COMPILE)gcc
endif

ARCH := $(shell uname -m)
ifeq ($(ARCH),riscv64)
    CFLAGS += -march=rv64gc
endif

SRC_DIR = src
INC_DIR = include
OBJ_DIR = obj
BIN_DIR = .

TARGET = $(BIN_DIR)/injector
TARGET_AARCH64 = $(BIN_DIR)/injector_aarch64

RV_C_SRCS = $(SRC_DIR)/injector_core.c $(SRC_DIR)/arch_riscv.c $(SRC_DIR)/ptrace_runner.c
RV_S_SRCS = $(SRC_DIR)/handler_trampoline.S
RV_OBJS = $(OBJ_DIR)/injector_core.o $(OBJ_DIR)/arch_riscv.o $(OBJ_DIR)/ptrace_runner.o \
          $(OBJ_DIR)/handler_trampoline.o

A64_C_SRCS = $(SRC_DIR)/injector_core.c $(SRC_DIR)/arch_aarch64.c $(SRC_DIR)/ptrace_runner.c
A64_S_SRCS = $(SRC_DIR)/handler_trampoline_aarch64.S
A64_OBJS = $(OBJ_DIR)/injector_core_a64.o $(OBJ_DIR)/arch_aarch64.o $(OBJ_DIR)/ptrace_runner_a64.o \
           $(OBJ_DIR)/handler_trampoline_aarch64.o

all: dirs $(TARGET)
	@chmod +x sifter.py summarize.py
	@echo ""
	@echo "Build complete: $(TARGET)"
	@echo "For AArch64 Linux: make injector_aarch64"
	@echo ""

dirs:
	@mkdir -p $(OBJ_DIR) $(BIN_DIR) data results

$(TARGET): $(RV_OBJS)
	$(CC) $(RV_OBJS) -o $@ $(LDFLAGS)

$(TARGET_AARCH64): $(A64_OBJS) | dirs
	$(CC_AARCH64) $(A64_OBJS) -o $@ $(LDFLAGS)
	@chmod +x sifter.py summarize.py
	@echo ""
	@echo "Build complete: $(TARGET_AARCH64)"
	@echo ""

.PHONY: build-aarch64
build-aarch64: $(TARGET_AARCH64)

$(RV_OBJS) $(A64_OBJS): | dirs

# RISC-V objects (default CC)
$(OBJ_DIR)/injector_core.o: $(SRC_DIR)/injector_core.c
	$(CC) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/arch_riscv.o: $(SRC_DIR)/arch_riscv.c
	$(CC) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/ptrace_runner.o: $(SRC_DIR)/ptrace_runner.c
	$(CC) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/handler_trampoline.o: $(SRC_DIR)/handler_trampoline.S
	$(CC) $(CFLAGS) -c $< -o $@

# AArch64 objects (separate injector_core.o to avoid clobbering RV build)
$(OBJ_DIR)/injector_core_a64.o: $(SRC_DIR)/injector_core.c
	$(CC_AARCH64) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/arch_aarch64.o: $(SRC_DIR)/arch_aarch64.c
	$(CC_AARCH64) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/ptrace_runner_a64.o: $(SRC_DIR)/ptrace_runner.c
	$(CC_AARCH64) $(CFLAGS) -I$(INC_DIR) -c $< -o $@

$(OBJ_DIR)/handler_trampoline_aarch64.o: $(SRC_DIR)/handler_trampoline_aarch64.S
	$(CC_AARCH64) $(CFLAGS) -c $< -o $@

clean:
	rm -rf $(OBJ_DIR) $(TARGET) $(TARGET_AARCH64)

distclean: clean
	rm -rf data/* results/*

deps:
	@echo "Installing dependencies..."
	sudo apt-get update
	sudo apt-get install -y libcapstone-dev libcapstone4 python3-pip
	pip3 install capstone

cross-deps:
	@echo "Installing RISC-V cross-compilation tools..."
	sudo apt-get update
	sudo apt-get install -y gcc-riscv64-linux-gnu

aarch64-cross-deps:
	@echo "Installing AArch64 cross-compiler (Debian/Ubuntu)..."
	sudo apt-get update
	sudo apt-get install -y gcc-aarch64-linux-gnu

test-build: dirs
	$(CC) $(CFLAGS) -I$(INC_DIR) -c $(SRC_DIR)/injector_core.c -o $(OBJ_DIR)/injector_core.o -UUSE_CAPSTONE
	@echo "Compiled injector_core.o without Capstone (no link). Use 'make' for full RISC-V injector."

run:
	sudo ./sifter.py --unk --dis --sync --tick

run-headless:
	sudo ./sifter.py --unk --dis --sync --tick --no-gui

run-quick:
	sudo ./sifter.py --unk --dis --sync --random --no-gui &
	sleep 10
	kill %1 2>/dev/null || true
	./summarize.py

analyze:
	./summarize.py data/log

report:
	./summarize.py data/log -d -o results/report.txt
	./summarize.py data/log -c results/results.csv

qemu-build:
	@./scripts/qemu-build.sh

qemu-scan:
	@./scripts/qemu-scan.sh

qemu-scan-random:
	@./scripts/qemu-scan.sh -r -n 10000

qemu-analyze:
	@./scripts/qemu-analyze.sh

docker-build:
	docker run --rm --privileged multiarch/qemu-user-static --reset -p yes || true
	docker build -f Dockerfile.riscv64 -t riscv-sifter .

docker-run:
	docker run --rm -it -v $(PWD)/data:/app/data riscv-sifter

macos-run:
	@./scripts/macos-docker-run.sh

macos-quick:
	@./scripts/macos-docker-run.sh quick

macos-shell:
	@./scripts/macos-docker-run.sh shell

macos-rebuild:
	@./scripts/macos-docker-run.sh rebuild

macos-deps:
	@echo "Installing macOS dependencies..."
	brew install qemu
	@echo ""
	@echo "Please also install Docker Desktop from:"
	@echo "  https://docker.com/products/docker-desktop/"
	@echo "  or: brew install --cask docker"

.PHONY: all dirs clean distclean deps cross-deps aarch64-cross-deps test-build run run-headless run-quick analyze report
.PHONY: qemu-build qemu-scan qemu-scan-random qemu-analyze docker-build docker-run
.PHONY: macos-run macos-quick macos-shell macos-rebuild macos-deps build-aarch64
