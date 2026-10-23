import pytest

pytest_plugins = ["pytester"]

# The assertion of single() shows its candidates in the report, as the tests that import it do theirs
pytest.register_assert_rewrite("tests.sequences")
