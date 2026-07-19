# How-to

Recipes for things the [Quickstart](../README.md#quickstart) and
[Workflow](../README.md#workflow) don't cover. Looking for how to read the report
itself — badges, sections, what's pre-checked and why? See
[Reading your report](report-guide.md).

## Scan multiple roots, including external drives

`scan` takes any number of paths in one run — they're deduplicated against each other
as a single pool, so a photo backed up to both your Mac and an external drive shows up
as a cross-root duplicate instead of two separate findings:

```sh
findupe scan ~/Pictures/inbox ~/Downloads "/Volumes/Extreme SSD/photos"
```

## Exclude paths

Pass `--exclude` (repeatable) with a glob to skip matching paths during discovery —
useful for a folder you know isn't worth scanning (caches, exports-in-progress, etc.):

```sh
findupe scan ~/Pictures --exclude "*/node_modules/*" --exclude "*/.git/*"
```

## Include iCloud-stub files in a scan

By default, files that are iCloud "dataless stubs" (present in Finder, not actually on
disk) are skipped and listed separately — findupe won't trigger a download on your
behalf unless you ask. Pass `--materialize` to have it read (and thus download) them:

```sh
findupe scan ~/Pictures --materialize
```

## Loosen or tighten the match threshold

`--threshold` controls how far into the "possible match" tier the review-only results
extend (max pHash distance). The default is calibrated against real photo libraries —
raise it to catch more borderline matches for manual review, lower it to see fewer:

```sh
findupe scan ~/Pictures --threshold 12
```

## Write the report somewhere specific

`-o`/`--output` sets the base path; findupe appends `-images.html` / `-other.html`:

```sh
findupe scan ~/Pictures -o ~/Desktop/inbox-report.html
# writes ~/Desktop/inbox-report-images.html and ~/Desktop/inbox-report-other.html
```

## Check scan history

Every `scan` is archived automatically. List past scans, or look up one by id:

```sh
findupe history                 # every archived scan, with reclaimable totals
findupe history 20260716-141203 # one scan's detail (id prefixes work too)
```

## View all-time stats, including a visual dashboard

`stats` totals everything across every scan and apply you've ever run:

```sh
findupe stats                        # text summary in the terminal
findupe stats --html                 # also writes findupe-dashboard.html
findupe stats --html ~/Desktop/dash.html   # custom output path
```

## Restore one specific file (not everything)

`findupe undo` with no argument lists restorable manifests. Pass one to restore
everything in it — including a single file's worth, if that's all a given `apply` run
touched:

```sh
findupe undo                     # list manifests
findupe undo 20260716-141203     # restore that run — full filename or a leading prefix works
```

## Clear the hash cache

Re-scans normally only hash files that are new or changed since the last scan. If the
cache ever seems stale or wrong (e.g. after restoring from an old backup), clear it —
the next scan just re-hashes everything from scratch:

```sh
findupe cache clear
```

## Configuration

You can save your settings in a persistent configuration file so you don't have to specify them every time you run a scan. The default location is `~/.findupe/config.toml`, but you can override this by setting the `FINDUPE_CONFIG` environment variable.

### Configuration Commands

Findupe provides subcommands to manage your settings:

```sh
findupe config                 # Print current config file path and resolved settings
findupe config init            # Initialize a new config file with the annotated template
findupe config init --force    # Re-initialize and overwrite an existing config file
findupe config get KEY         # Print the value of a specific setting
findupe config set KEY VALUE   # Set a scalar setting (e.g. threshold, no_ocr)
findupe config add-root PATH   # Add a default directory to the roots list
findupe config add-exclude GLOB # Add a default glob pattern to the exclude list
```

To remove default roots or excludes, simply edit the configuration TOML file by hand.

### Precedence and Stacking Rules

When you run a scan, settings are resolved using the following order of precedence:
1. **CLI flag/argument** (highest precedence)
2. **Configuration file**
3. **Built-in default** (lowest precedence)

#### Special Merging Rules
- **Roots**: Specifying paths on the CLI **replaces** your configured default roots.
- **Excludes**: Excludes specified on the CLI are **added to** (stacked with) your configured excludes. This prevents standing excludes from being silently dropped.
