"""PyInstaller GUI entry point. Keep the imported module discoverable."""

from multiprocessing import freeze_support

from relay_audit.portable import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
