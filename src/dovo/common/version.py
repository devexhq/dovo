from importlib import metadata


def get_version():
    """Get the package version from importlib."""
    try:
        # This must exactly match the 'name' string inside your pyproject.toml
        version_string = metadata.version("devexhq-dovo")
    except metadata.PackageNotFoundError:
        # Fallback for when running the raw script without installing the package
        version_string = "0.1.1-local-dev"
    return version_string
