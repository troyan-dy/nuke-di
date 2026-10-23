import pytest

pytest_plugins = ["pytester"]

# The helpers of tests.sequences assert, and pytest explains a failed assert only in a rewritten module
pytest.register_assert_rewrite("tests.sequences")
