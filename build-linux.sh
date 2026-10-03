#!/usr/bin/env bash
# Build local de Codex Android ; aucun dépôt TUR n'est utilisé.
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
arch="${1:-aarch64}"
version="${2:-${CODEX_VERSION:-0.160.0}}"
[[ "$arch" == aarch64 || "$arch" == x86_64 ]] || { echo 'Architecture : aarch64 ou x86_64' >&2; exit 1; }
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Le build nécessite Linux x86_64 et Docker ; le résultat s’installe sur Android.' >&2; exit 1; }
for cmd in git uv curl sha256sum dpkg-deb patch; do command -v "$cmd" >/dev/null || { echo "Commande requise : $cmd" >&2; exit 1; }; done
uv run --no-project --python 3.11 "$root/tools.py" validate-version "$version"
build_root="$root/.build/$version"
mkdir -p "$build_root" "$root/dist"
recipe="$build_root/recipe/codex-termux"
mkdir -p "$recipe"
cp -a "$root/recipe/codex-termux/." "$recipe/"
printf 'Version Codex demandée : %s\n' "$version"
if ! curl -fsSL --proto '=https' --proto-redir '=https' --retry 3 \
    --connect-timeout 10 --max-time 60 \
    "https://api.github.com/repos/openai/codex/releases/tags/rust-v$version" -o "$build_root/release.json"; then
    echo "Release Codex $version inaccessible. Le préfixe rust-v est celui des tags Codex, pas une version du compilateur Rust. Aucun remplacement par une autre version." >&2
    exit 1
fi
curl -fsSL --proto '=https' --proto-redir '=https' --retry 3 \
    --connect-timeout 10 --max-time 600 \
    "https://github.com/openai/codex/archive/refs/tags/rust-v$version.tar.gz" -o "$build_root/source.tar.gz"
uv run --no-project --python 3.11 "$root/tools.py" prepare-recipe \
    "$build_root/release.json" "$build_root/source.tar.gz" "$recipe" "$version" "$build_root/source.json"
# Les versions de patches incompatibles et les tags absents s’arrêtent avant Docker.
command -v docker >/dev/null || { echo 'Commande requise : docker' >&2; exit 1; }
docker info >/dev/null
framework="$build_root/termux-packages"
if [[ ! -d "$framework" ]]; then
    git clone --depth 1 --branch master https://github.com/termux/termux-packages.git "$framework"
fi
[[ -x "$framework/scripts/run-docker.sh" ]] || { echo 'Framework Termux incomplet.' >&2; exit 1; }
if [[ -f "$build_root/framework.commit" ]]; then
    [[ "$(git -C "$framework" rev-parse HEAD)" == "$(cat "$build_root/framework.commit")" ]] || { echo 'Le commit du framework a changé ; utilisez un nouveau dossier de build.' >&2; exit 1; }
else
    git -C "$framework" rev-parse HEAD > "$build_root/framework.commit"
fi

# Verrouiller l’image par digest au premier build, puis la réutiliser.
if [[ ! -f "$build_root/image.digest" ]]; then
    image="${CODEX_BUILDER_IMAGE:-ghcr.io/termux/package-builder:latest}"
    docker pull "$image"
    docker image inspect --format '{{index .RepoDigests 0}}' "$image" > "$build_root/image.digest"
fi
image="$(cat "$build_root/image.digest")"
[[ "$image" == *@sha256:* ]] || { echo 'Digest Docker absent.' >&2; exit 1; }
docker pull "$image"

support="$framework/codex-support"
mkdir -p "$support/bin" "$framework/custom-packages/codex-termux"
cp -a "$recipe/." "$framework/custom-packages/codex-termux/"
cp "$root/tools.py" "$root/in-container.sh" "$root/TUR-COMMIT.txt" "$build_root/framework.commit" "$build_root/image.digest" "$build_root/source.json" "$support/"
cp "$(command -v uv)" "$support/bin/uv"
chmod 755 "$support/bin/uv" "$support/in-container.sh"
if [[ -n "${CODEX_RUST_TOOLCHAIN:-}" ]]; then
    printf '%s\n' "$CODEX_RUST_TOOLCHAIN" > "$support/toolchain.request"
elif [[ -f "$framework/output/codex-termux-toolchain.lock" ]]; then
    cp "$framework/output/codex-termux-toolchain.lock" "$support/toolchain.request"
else
    # Suivre la version exacte déclarée dans les sources Codex inspectées.
    uv run --no-project --python 3.11 "$root/tools.py" source-toolchain "$support/source.json" > "$support/toolchain.request"
fi
uv run --no-project --python 3.11 "$root/tools.py" validate-toolchain "$(cat "$support/toolchain.request")"

# Un nom différent par dossier/architecture évite de réutiliser un conteneur
# Termux monté sur le répertoire d’un autre projet.
container_key="$(printf '%s' "$root" | sha256sum | cut -c 1-12)"
export CONTAINER_NAME="codex-termux-$container_key-$version-$arch"
export TERMUX_BUILDER_IMAGE_NAME="$image"
(
    cd "$framework"
    ./scripts/run-docker.sh ./codex-support/in-container.sh "$arch"
)

uv run --no-project --python 3.11 "$root/tools.py" package \
    "$framework/output" "$root/dist" "$arch"
printf '\nArtefacts prêts dans : %s/dist\n' "$root"
