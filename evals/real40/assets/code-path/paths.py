from pathlib import Path
def safe_path(root, relative):
    return str(Path(root) / relative)
