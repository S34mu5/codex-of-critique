from pathlib import PurePosixPath


def file_extension_from_path(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    return suffix or None
