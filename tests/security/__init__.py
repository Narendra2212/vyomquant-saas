# Package marker for tests.security.
#
# strategy-builder task 8.7. `pytest.ini`'s `norecursedirs` excludes only `tests/perf`
# and the shipped defaults, so this directory IS collected by the default `pytest -q`
# lane - which is the point: an isolation matrix that has to be asked for is a matrix
# nobody runs.
