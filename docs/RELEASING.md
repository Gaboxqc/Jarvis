# Releasing

Updates are served from GitHub Releases on `Gaboxqc/Jarvis`. The app checks
`releases/latest/download/latest.json` — but only when someone presses the
button in Settings. Nothing here happens on launch.

## The signing key, first

There is exactly one secret in this project, and it is not recoverable.

```
C:\Users\Gabox\.tauri\kai-updater.key       <- private. Back this up.
C:\Users\Gabox\.tauri\kai-updater.key.pub   <- public. Already in tauri.conf.json.
```

**Back up the private key somewhere that is not this machine.** A password
manager, an encrypted drive, anywhere you will still have it in two years.

Losing it is not an inconvenience. Every installed copy of Kai carries the
matching public key compiled in, and refuses any update that does not verify
against it. A new key produces updates that every existing install rejects as
forgeries, and the only way out is asking each user to uninstall and reinstall
by hand.

Anyone who *has* it can push signed code to every install, and those installs
will accept it without question — this app reads your mail, your files and your
calendar, so treat the key accordingly. It is gitignored, and a test in
`test_packaging.py` fails the build if key material ever appears in the
repository. That test was written, planted with a real key to confirm it
actually fires, and only then trusted.

## Cutting a release

Every build ships as a release. Not a habit -- the app checks
`releases/latest/download/latest.json` and nothing else, so a build that is not
published there does not exist as far as any installed copy is concerned.

1. **Bump the version in five files.** The first three must match or the updater
   compares the wrong numbers; the two lockfiles carry it as well, and leaving
   them behind means the next `npm install` or `cargo build` quietly rewrites
   them and dirties the tree:

   ```
   ui/package.json
   ui/package-lock.json          <- twice: the top level and the "" package
   ui/src-tauri/Cargo.toml
   ui/src-tauri/Cargo.lock       <- the [[package]] entry named "kai"
   ui/src-tauri/tauri.conf.json
   ```

2. **Build.** The script picks up the signing key automatically:

   ```powershell
   powershell -ExecutionPolicy Bypass -File installeruild.ps1
   ```

   It refuses to package a bundle whose self-test fails. Where an Application
   Control policy blocks running a freshly built unsigned binary, the self-test
   cannot run at all; `-SkipSelfTest` says so loudly and the static check on
   skill collection still runs.

3. **Publish.**

   ```powershell
   .venv\Scripts\python installer\publish.py --notes "what changed"
   ```

   It writes `latest.json`, creates the tag, uploads the installer, the `.sig`
   and the manifest, and marks the release latest. It refuses when the version
   files disagree, when the `.sig` is missing, or when the tag already exists
   without `--replace` -- each of those produces a release that looks complete
   and cannot update anybody.

   To attach the voice engine: `--asset path	o\kai-xtts-<version>-x64.zip`.

4. **Check it from outside.**

   ```bash
   curl -sIL https://github.com/Gaboxqc/Jarvis/releases/latest/download/latest.json
   ```

   200 means installed copies can see it. This is worth doing every time: the
   first three releases of this app returned 404 here for months.

### Do not mark a release as a pre-release

GitHub excludes pre-releases from `releases/latest`, which is the exact URL the
updater reads. v0.1-beta, v0.2-beta and v0.3-beta were all pre-releases, none
carried a `.sig` or a manifest, and the endpoint 404'd throughout. From inside
the app that is indistinguishable from "you are up to date", so nothing ever
reported it. `publish.py` marks releases latest unless told otherwise, and says
what `--prerelease` costs.

## If a release goes wrong

Un-mark it as latest on GitHub. The endpoint follows whatever is marked latest,
so the previous release becomes the offered update again immediately — there is
nothing to roll back in the app.
