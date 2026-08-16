# Recovery and rollback

Each changed path is backed up during a successful apply, and the backup directory is printed in the command output. Keep that workspace-relative backup directory until `verify` is clean.

If an apply fails after it starts writing, the installer automatically restores the changed paths from its temporary backup. Inspect the error, run `plan` again, and correct the underlying configuration or ownership conflict before retrying.

To explicitly restore a completed apply, use its printed backup directory:

```sh
memory-system rollback --backup .memory-system/backups/<backup-id> --workspace .
memory-system verify --workspace .
```

Rollback restores only the files recorded by that installation. It does not recover or move official runtime history, and it does not modify curated content that was never part of a managed change. If a backup is missing or invalid, stop and recover the workspace from its normal filesystem backup before attempting a new apply.
