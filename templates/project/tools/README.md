# Inspection tool example

The example is exposed as a frozen source reference. The agent copies its text to
`workspace/scripts/example.py` inside its task, then calls `python` with the script
path and a JSON argument array. It does not execute directly from this template
directory. All fitting belongs to `training`; arbitrary Python still has host
process permissions and is not a secure read-only sandbox.
