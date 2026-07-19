# FAQ

### Is anything ever permanently deleted?

No. `apply` moves files to the real macOS Trash (Finder's "Put Back" works), never
`rm`. The last copy of anything always survives — enforced independently of what the
report UI shows you — and `findupe undo` restores from the Trash by content (size +
hash), not by guessing names. See [Safety model](architecture.md#safety-model) for the
full list of guarantees.

### How are RAW+JPEG pairs handled?

They're never flagged. Keeping `X.CR3` and `X.jpg` side by side is normal for
photographers — one for editing, one for sharing — so cross-format siblings are shown
in the report as informational context only. Only *surplus copies within the same
format* (`X copy.jpg`, `X_2.jpg`, a re-imported CR3) become deletion candidates. See
[Architecture](architecture.md) for how the matching tiers work.

### What does the ☁ badge mean?

The file lives in an iCloud Drive, Desktop & Documents, or Dropbox-synced folder.
Deleting it doesn't just move it to your Mac's Trash — it propagates to your other
devices too. The badge is a heads-up before you check that box, not a block. See
[Reading your report](report-guide.md) for every badge the report can show.

### What happens if a scan is interrupted?

Nothing partial is deleted — `scan` never has delete authority in the first place.
Ctrl-C during a scan exits cleanly; the hash cache keeps whatever it already computed,
so re-running the same scan only re-hashes new or changed files instead of starting
over.

### Why did macOS ask permission for "Terminal wants to control Finder"?

That's the Trash integration — findupe moves files by asking Finder to do it (via
AppleScript), which is what makes "Put Back" work. It's a one-time prompt on your
first `apply`. If you deny it, `apply` aborts safely without touching anything; you
can re-approve later in System Settings → Privacy & Security → Automation.

### Does findupe scan inside Photos or Lightroom libraries?

No — managed library internals (`.photoslibrary`, `.lrlib`/`.lrdata`/`.lrcat`, etc.) are
a hard denylist, refused with an explanation rather than silently skipped. There's no
flag to override this.

### Is there a GUI?

Not currently. Review happens in the self-contained HTML report `scan` generates —
open it in any browser, no server or install needed.

### Can I schedule scans automatically?

Not built in. Nothing stops you from calling `findupe scan` from `cron`/`launchd`
yourself, but findupe doesn't manage a schedule for you.

### Does it use OCR to catch duplicate screenshots?

Yes, but only as a safety net. For screenshot pairs that pHash already marked as strong
matches, macOS Vision OCR reads on-screen text. If the text differs significantly,
findupe demotes the pair to review-only rather than suggesting it as a duplicate. See
[Screenshot text discrimination](architecture.md#screenshot-text-discrimination-macos-vision-ocr)
for the full mechanism.

### Can I save my settings or scan roots?

Yes. Run `findupe config init` to write a default configuration template to `~/.findupe/config.toml`. You can configure default scan roots, glob excludes, threshold values, and output directories. See the [How-to](how-to.md#configuration) guide for details on configuration commands and precedence rules.

## See also

- [Reading your report](report-guide.md) — sections, badges, and flags explained
- [Architecture](architecture.md) — matching tiers and the full safety model
- [How-to](how-to.md) — CLI recipes beyond the basic scan → review → apply flow
