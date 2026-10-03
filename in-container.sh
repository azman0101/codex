#!/usr/bin/env bash
set -Eeuo pipefail
cd /home/builder/termux-packages
arch="${1:?Architecture requise}"
[[ "$arch" == aarch64 || "$arch" == x86_64 ]]
support="$PWD/codex-support"
"$support/bin/uv" run --offline --no-project --python /usr/bin/python3 \
    python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 minimum requis dans le conteneur"'
toolchain="$(cat "$support/toolchain.request")"
mkdir -p output
printf '%s\n' "$toolchain" > output/codex-termux-toolchain.lock
export CODEX_RUST_TOOLCHAIN="$toolchain"
export CODEX_SUPPORT_DIR="$support"
export CODEX_FRAMEWORK_COMMIT="$(cat "$support/framework.commit")"
export CODEX_BUILDER_IMAGE="$(cat "$support/image.digest")"
export CODEX_TUR_COMMIT="$(cat "$support/TUR-COMMIT.txt")"
export CODEX_BUILD_METADATA="$PWD/output/codex-termux-build-$arch.json"
# Dossier de paquet local ; -I ne télécharge que les dépendances du framework
# officiel Termux. Il ne télécharge pas Codex depuis TUR.
./build-package.sh -f -I -a "$arch" --format debian -o "$PWD/output" \
    ./custom-packages/codex-termux
