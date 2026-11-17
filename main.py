"""Default Guix agent entry point. See: uv run python /app/main.py --help."""

from cli import main

if __name__ == "__main__":
    raise SystemExit(main())
