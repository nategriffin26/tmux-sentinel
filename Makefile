# Make predefines CC=cc, so `?=` never takes effect. Honour an explicit
# `make CC=...` from the command line or environment, otherwise pick a
# sensible per-platform default.
ifeq ($(origin CC),default)
  ifeq ($(shell uname -s),Darwin)
    CC := clang
  else
    CC := cc
  endif
endif
CFLAGS ?= -O2 -Wall -Wextra -Wpedantic -std=c11
CPPFLAGS ?=
LDFLAGS ?=
PREFIX ?= $(HOME)/.local
UNAME_S := $(shell uname -s)

ifeq ($(UNAME_S),Darwin)
PLATFORM_LDLIBS := -framework IOKit -framework CoreFoundation
else ifeq ($(UNAME_S),Linux)
PLATFORM_LDLIBS :=
else
$(error Unsupported platform: $(UNAME_S))
endif

SHELLSCRIPTS := sentinel.tmux $(wildcard scripts/*.sh) $(wildcard assets/*.sh)

.PHONY: all install install-bin uninstall test clean lint bench release

all: bin/sentinel-status

bin/sentinel-status: src/sentinel-status.c
	@mkdir -p bin
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(PLATFORM_LDLIBS)

install-bin: all
	@mkdir -p $(PREFIX)/bin
	install -m 0755 bin/sentinel-status $(PREFIX)/bin/sentinel-status
	ln -sf $(CURDIR)/bin/sentinel $(PREFIX)/bin/sentinel

install: install-bin
	@$(CURDIR)/bin/sentinel install

uninstall:
	rm -f $(PREFIX)/bin/sentinel-status $(PREFIX)/bin/sentinel

# Main owns the project-wide test suite; this canonical entry point is for CI/release use.
test: all
	python3 -m unittest discover -s test -v

clean:
	rm -f bin/sentinel-status

lint:
	$(CC) $(CPPFLAGS) -O2 -Wall -Wextra -Wpedantic -std=c11 -fsyntax-only src/sentinel-status.c
	@command -v shellcheck >/dev/null 2>&1 \
		&& shellcheck $(SHELLSCRIPTS) \
		|| echo "shellcheck not installed; skipping shell lint" >&2
	python3 -m compileall -q cli

# Interleaved A/B against the v0.1 shell engine, so both see the same load.
bench: all
	@python3 test/bench.py

release:
ifeq ($(UNAME_S),Darwin)
	@mkdir -p bin
	$(CC) $(CPPFLAGS) $(CFLAGS) -arch arm64 -arch x86_64 $(LDFLAGS) -o bin/sentinel-status src/sentinel-status.c $(PLATFORM_LDLIBS)
	lipo -info bin/sentinel-status
else
	@echo "release is supported only on Darwin" >&2; exit 1
endif
