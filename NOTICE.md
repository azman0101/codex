Native macOS build launcher and driver created for the Codex 0.162.0 Android port, 2026-10-03.

`verified_tools.py`, `install-codex-termux.sh` and the existing patches are adapted/copied from azman0101/codex at f09736bd5da206635ad7336af79c04f512a9db7f, incorporating the previously inspected Termux/TUR porting patches. This bundle does not install or activate TUR.

Test fixtures preserve upstream source headers and are used to verify patch compatibility:

- `cc-lib.rs`: rust-lang/cc-rs crate 1.2.55, MIT OR Apache-2.0.
- `v8-build.rs`: denoland/rusty_v8 v150.4.0, MIT.
- `android-config.gni` and `run_bindgen.py`: denoland/chromium_build commit 8acb33ac8dceef0503443109c0a92988189563ef, Chromium BSD license.

The build gathers Codex, rusty_v8 and V8 licenses into each produced Android archive. Third-party tools and Android NDK retain their own licenses. The NDK download and use are governed by Google's Android SDK License Agreement at https://developer.android.com/ndk/downloads.

Codex CLI is developed by OpenAI at https://github.com/openai/codex and licensed under the Apache License 2.0; a copy of the upstream license (https://github.com/openai/codex/blob/main/LICENSE) is included in this repository as `LICENSE`. This repository contains only build tooling and patches; it is not affiliated with or endorsed by OpenAI.
