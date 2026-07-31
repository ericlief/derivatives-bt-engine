.PHONY: install install-ib install-all

# Main package only -- everything except the live/IB-connected paths
# (see pyproject.toml's own comment on why ib_tools isn't a static
# dependency here).
install:
	.venv/bin/pip install -e .

# ib_tools is a separate, local sibling repo -- PROJECT_DIR must point at
# the parent directory holding your checkout of it (e.g. /home/dev/projects,
# if your layout is $PROJECT_DIR/fin-tools/ib-tools).
install-ib:
	@if [ -z "$$PROJECT_DIR" ]; then \
		echo "PROJECT_DIR is not set -- export PROJECT_DIR=/path/to/your/projects" \
		     "(the parent directory holding your fin-tools/ib-tools checkout) and re-run" >&2; \
		exit 1; \
	fi
	.venv/bin/pip install -e "$$PROJECT_DIR/fin-tools/ib-tools"

# Both, in one step, for the live/IB-connected paths.
install-all: install install-ib
