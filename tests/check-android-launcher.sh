#!/usr/bin/env bash
set -eo pipefail

if [[ $# != 2 || ( $1 != prepare && $1 != verify ) ]]; then
    printf 'Usage: bash %s prepare|verify unique-fixture-id\n' "$0" >&2
    exit 2
fi
mode=$1
fixture_id=$2
[[ "$fixture_id" =~ ^t3-android-ci-[A-Za-z0-9-]+$ ]]
flatpak_home=$HOME
app_data="$flatpak_home/.var/app/com.t3tools.t3code/data"
data_home="$app_data/$fixture_id"

# Flatpak resets XDG variables on startup; set these in this disposable app
# process before the launcher writes any state.
export HOME="$data_home/home"
export XDG_DATA_HOME="$data_home"
export XDG_CONFIG_HOME="$data_home/config"
export XDG_CACHE_HOME="$data_home/cache"
export XDG_STATE_HOME="$data_home/state"
for state_path in "$HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME"; do
    case "$state_path" in
        "$data_home"|"$data_home"/*) ;;
        *) printf 'Refusing non-fixture Android state path: %s\n' "$state_path" >&2; exit 1 ;;
    esac
done

android_data="$data_home/t3code/android"
sdk_current="$android_data/sdk/current"
java_current="$android_data/java/current"
if [[ "$mode" == prepare ]]; then
    if [[ -e "$android_data" || -L "$android_data" ]]; then
        echo 'Android launcher fixture unexpectedly already exists.' >&2
        exit 1
    fi
    mkdir -p "$android_data/sdk/versions/fixture" "$android_data/java/versions/fixture/bin"
    printf '#!/bin/sh\nexit 0\n' > "$android_data/java/versions/fixture/bin/java"
    chmod 755 "$android_data/java/versions/fixture/bin/java"
    ln -s versions/fixture "$sdk_current"
    ln -s versions/fixture "$java_current"
else
    [[ -d "$sdk_current" && -x "$java_current/bin/java" ]]
    [[ -d "$android_data/user/avd" ]]
fi

expected_sdk="$sdk_current"
expected_java="$java_current"
expected_user="$android_data/user"
expected_avd="$expected_user/avd"
assert_launcher_env() {
    [[ "$ANDROID_HOME" == "$expected_sdk" ]]
    [[ "$ANDROID_SDK_ROOT" == "$expected_sdk" ]]
    [[ "$JAVA_HOME" == "$expected_java" ]]
    [[ "$ANDROID_USER_HOME" == "$expected_user" ]]
    [[ "$ANDROID_AVD_HOME" == "$expected_avd" ]]
    [[ -d "$ANDROID_USER_HOME" && -d "$ANDROID_AVD_HOME" ]]
    [[ "$PATH" == *"$JAVA_HOME/bin"* ]]
}
zypak-wrapper() {
    assert_launcher_env
    [[ "$EXPO_DEVICE_HUB_EMULATOR_EXTRA_ARGS" == "$expected_emulator_args" ]]
}
unset ANDROID_HOME ANDROID_SDK_ROOT JAVA_HOME ANDROID_USER_HOME ANDROID_AVD_HOME
unset EXPO_DEVICE_HUB_EMULATOR_EXTRA_ARGS
expected_emulator_args='-gpu swangle'
source /app/bin/startt3code
assert_launcher_env

# Existing extras survive verbatim; the later explicit GPU option wins when
# the hub tokenizes this value and appends it after its own `-gpu auto`.
export EXPO_DEVICE_HUB_EMULATOR_EXTRA_ARGS='-no-snapshot -gpu angle'
expected_emulator_args='-gpu swangle -no-snapshot -gpu angle'
source /app/bin/startt3code
read -r -a effective_emulator_args <<< "$EXPO_DEVICE_HUB_EMULATOR_EXTRA_ARGS"
[[ "${effective_emulator_args[*]}" == '-gpu swangle -no-snapshot -gpu angle' ]]
[[ "${effective_emulator_args[-1]}" == angle ]]

# Explicit SDK, Java, preferences, and AVD paths take precedence over private defaults.
override_root="$data_home/overrides"
mkdir -p "$override_root/sdk" "$override_root/java/bin" "$override_root/user" "$override_root/avd"
expected_sdk="$override_root/sdk"
expected_java="$override_root/java"
expected_user="$override_root/user"
expected_avd="$override_root/avd"
unset ANDROID_HOME ANDROID_SDK_ROOT JAVA_HOME ANDROID_USER_HOME ANDROID_AVD_HOME
export ANDROID_SDK_ROOT="$expected_sdk"
export JAVA_HOME="$expected_java"
export ANDROID_USER_HOME="$expected_user"
export ANDROID_AVD_HOME="$expected_avd"
unset EXPO_DEVICE_HUB_EMULATOR_EXTRA_ARGS
expected_emulator_args='-gpu swangle'
source /app/bin/startt3code
assert_launcher_env

printf 'Android launcher %s checks passed.\n' "$mode"
