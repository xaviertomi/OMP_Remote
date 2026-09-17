# Android installation

## Build prerequisites

Install a supported JDK 21, Android SDK platform/build tools, and Gradle 8.11.1 or generate the Gradle wrapper from the checked-in wrapper properties. The project compiles Java/Kotlin bytecode targeting JVM 17 while using the installed JDK 21 toolchain.

From the repository:

```bash
cd android
gradle assembleDebug
adb install -r app/build/outputs/apk/debug/omp-remote-v0.5.apk
```

The versioned debug APK is built at `android/app/build/outputs/apk/debug/omp-remote-v0.5.apk`; only release signing and physical-device validation remain pending. No signing key is stored in the repository.

## Version and server publication

The server source `server/omp_remote/version.py` is the single version source. The Android Gradle build reads `VERSION_CODE` and `VERSION_NAME` from that file, so the visible title, APK metadata, update metadata, and versioned filename remain aligned. Do not publish an APK without rebuilding it from the current checkout.

After `gradle assembleDebug`, publish both the current server code and the versioned debug APK with a local root shell:

```bash
sudo /home/tomi/omp_app/scripts/repair-tomi-install.sh
```

The repair script stores the versioned APK as `/var/lib/omp-remote/client/omp-remote-v0.5.apk` and refreshes the stable bootstrap path `/var/lib/omp-remote/client/omp-remote.apk`, used by `OMP_REMOTE_APK_PATH`. Download responses advertise the versioned filename.

## Phone-only initial installation

After the root repair script publishes the APK and Tailscale Serve is configured as `tailnet only`, open this URL on the phone:

```text
https://mendel.tailedc637.ts.net/download/omp-remote.apk
```

Allow installation from the browser when Android requests it, install the APK, then disable that permission again if desired. The bootstrap download is reachable only through the private Tailscale tailnet; subsequent update checks require the normal authenticated credential.

## First setup

1. Enter the HTTPS server URL.
2. Enter the normal device credential issued for the active Unix profile.
3. Enter the separate emergency credential if emergency control is required.
4. Save the profile. Credentials are encrypted with Android Keystore.
5. Confirm the displayed profile (`tomi` in the current scope).
6. Submit a harmless OMP prompt and verify status/log/file behavior.

The app has no free-form run-as selector and contains no enable action. Emergency kill requires deliberate confirmation and must be followed by emergency status inspection; re-enable is local-root-only.

## Conversation workflow

The app manages server-owned conversations. The first message calls `POST /api/v1/conversations`; later messages call `POST /api/v1/conversations/{conversation_id}/messages` and remain in the same conversation. `GET /api/v1/conversations/{conversation_id}` returns each turn, its technical job, state, stdout, stderr, and error. The Android client polls the conversation detail while a turn is queued or running, keeps the draft and selected conversation across rotation, and places the composer directly below the active dialogue. Jobs remain available for files, diagnostics, and targeted cancellation; they are implementation details, not separate conversations.
## Bottom navigation

The app has exactly three persistent bottom buttons:

- **Conversation**: send prompts, see local history, follow live status/output, and access files for the selected job.
- **Application**: reserved empty area showing `Les fonctionnalités à venir apparaîtront ici.`
- **Kill**: select a queued/running job for targeted cancellation; emergency kill remains separately confirmed.

## Updates and revocation

Install a new APK with `adb install -r`. Revoke lost-device credentials locally, issue replacements, and never paste credentials into URLs, source, resources, screenshots, or logs.
