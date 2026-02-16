import pytest

# Simple 3-package workspace: api → shared, worker → shared
SIMPLE_LOCK = """\
version = 1

[manifest]
members = ["api", "shared", "worker"]

[[package]]
name = "api"
version = "0.1.0"
source = { editable = "packages/api" }
dependencies = [
    { name = "shared" },
    { name = "requests" },
]

[[package]]
name = "shared"
version = "0.1.0"
source = { editable = "packages/shared" }
dependencies = []

[[package]]
name = "worker"
version = "0.1.0"
source = { editable = "packages/worker" }
dependencies = [
    { name = "shared" },
]

[[package]]
name = "requests"
version = "2.31.0"
source = { registry = "https://pypi.org/simple" }
"""

# Diamond dependency: app → api → shared, app → worker → shared
DIAMOND_LOCK = """\
version = 1

[manifest]
members = ["app", "api", "shared", "worker"]

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "packages/app" }
dependencies = [
    { name = "api" },
    { name = "worker" },
]

[[package]]
name = "api"
version = "0.1.0"
source = { editable = "packages/api" }
dependencies = [
    { name = "shared" },
]

[[package]]
name = "shared"
version = "0.1.0"
source = { editable = "packages/shared" }
dependencies = []

[[package]]
name = "worker"
version = "0.1.0"
source = { editable = "packages/worker" }
dependencies = [
    { name = "shared" },
]
"""

# Virtual root workspace (root package is virtual)
VIRTUAL_ROOT_LOCK = """\
version = 1

[manifest]
members = ["myproject", "api", "lib"]

[[package]]
name = "myproject"
version = "0.1.0"
source = { virtual = "." }
dependencies = [
    { name = "api" },
    { name = "lib" },
]

[[package]]
name = "api"
version = "0.1.0"
source = { directory = "packages/api" }
dependencies = [
    { name = "lib" },
]

[[package]]
name = "lib"
version = "0.1.0"
source = { directory = "packages/lib" }
dependencies = []
"""

# Lock with optional and dev dependencies
OPTIONAL_DEV_LOCK = """\
version = 1

[manifest]
members = ["api", "shared", "worker"]

[[package]]
name = "api"
version = "0.1.0"
source = { editable = "packages/api" }
dependencies = [
    { name = "shared" },
]

[package.optional-dependencies]
extra = [
    { name = "worker" },
]

[package.dev-dependencies]
dev = [
    { name = "worker" },
]

[[package]]
name = "shared"
version = "0.1.0"
source = { editable = "packages/shared" }
dependencies = []

[[package]]
name = "worker"
version = "0.1.0"
source = { editable = "packages/worker" }
dependencies = []
"""


@pytest.fixture
def simple_lock(tmp_path):
    lock_file = tmp_path / "uv.lock"
    lock_file.write_text(SIMPLE_LOCK)
    return lock_file


@pytest.fixture
def diamond_lock(tmp_path):
    lock_file = tmp_path / "uv.lock"
    lock_file.write_text(DIAMOND_LOCK)
    return lock_file


@pytest.fixture
def virtual_root_lock(tmp_path):
    lock_file = tmp_path / "uv.lock"
    lock_file.write_text(VIRTUAL_ROOT_LOCK)
    return lock_file


@pytest.fixture
def optional_dev_lock(tmp_path):
    lock_file = tmp_path / "uv.lock"
    lock_file.write_text(OPTIONAL_DEV_LOCK)
    return lock_file
