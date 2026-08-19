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

1. **Bump the version in five files.** The first three must match or the
   updater compares the wrong numbers; the two lockfiles carry the version as
   well, and leaving them behind means the next `npm install` or `cargo build`
   quietly rewrites them and dirties the tree:

   ```
   ui/package.json
   ui/package-lock.json          <- twice: the top level and the "" package
   ui/src-tauri/Cargo.toml
   ui/src-tauri/Cargo.lock       <- the [[package]] entry named "kai"
   ui/src-tauri/tauri.conf.json
   ```

2. **Build.** The script picks up the signing key automatically:

   ```powershell
   powershell -ExecutionPolicy Bypass -File installer\build.ps1
   ```

   It refuses to package a bundle whose self-test fails, which is the guard
   that catches a build shipping one skill out of forty-eight.

3. **Collect the artifacts** from `ui/src-tauri/target/release/bundle/nsis/`:

   ```
   Kai Assistant_<version>_x64-setup.exe
   Kai Assistant_<version>_x64-setup.exe.sig
   ```

   No `.sig` means the key was not found and the build printed a warning. That
   installer works, but no existing install will accept it as an update.

4. **Write `latest.json`.** `signature` is the *contents* of the `.sig` file,
   not a path:

   ```json
   {
     "version": "0.3.0",
     "notes": "What changed, in a sentence someone would want to read.",
     "pub_date": "2026-08-14T12:00:00Z",
     "platforms": {
       "windows-x86_64": {
         "signature": "<contents of the .sig file>",
         "url": "https://github.com/Gaboxqc/Jarvis/releases/download/v0.3.0/Kai.Assistant_0.3.0_x64-setup.exe"
       }
     }
   }
   ```

   The `url` must be the direct download for *that* release tag, not the
   `latest` alias — the manifest is version-specific even though the endpoint
   that serves it is not.

5. **Publish the release** with the tag `v<version>`, attaching the installer,
   the `.sig`, and `latest.json`. Mark it as the latest release; the endpoint
   resolves through that.

6. **Check it from an older install** before telling anyone. Settings →
   Updates → Check now. An update that silently fails to verify looks identical
   to no update being available.

## If a release goes wrong

Un-mark it as latest on GitHub. The endpoint follows whatever is marked latest,
so the previous release becomes the offered update again immediately — there is
nothing to roll back in the app.
