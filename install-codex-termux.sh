#!/data/data/com.termux/files/usr/bin/bash
# Installe un artefact Android construit par build-linux.sh ; sans dépôt TUR.
set -Eeuo pipefail
die() { printf 'Erreur : %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }
usage() {
    cat <<'EOF'
Usage : bash install-codex-termux.sh --archive FICHIER [--sha256 HEX] [--yes] [--replace]
    ou : bash install-codex-termux.sh --url URL_HTTPS --sha256 HEX [--yes] [--replace]

--archive : archive Android produite par build-linux.sh.
--sha256  : empreinte de l’archive ; lit FICHIER.sha256 si disponible en mode local.
--yes     : accepte les installations de dépendances officielles Termux.
--replace : sauvegarde une installation manuelle existante avant remplacement.
            Les fichiers appartenant à un paquet APT ne sont jamais écrasés.

Relancez la même commande avec une nouvelle archive pour mettre à jour.
EOF
}
archive='' url='' expected='' yes=false replace=false
while (($#)); do
    case "$1" in
        --archive|--url|--sha256)
            (($# >= 2)) || die "$1 nécessite une valeur."
            case "$1" in --archive) archive="$2";; --url) url="$2";; --sha256) expected="$2";; esac
            shift 2 ;;
        --yes|-y) yes=true; shift ;;
        --replace) replace=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "Argument inconnu : $1" ;;
    esac
done
[[ -n "$archive" || -n "$url" ]] || die 'Fournissez --archive ou --url.'
[[ -z "$archive" || -z "$url" ]] || die '--archive et --url sont exclusifs.'
if [[ -n "$url" ]]; then
    [[ "$url" == https://* && -n "$expected" ]] || die '--url nécessite HTTPS et --sha256.'
fi
[[ -n "${PREFIX:-}" && "$PREFIX" == */com.termux/files/usr ]] || die 'Termux standard requis.'
[[ -n "${TERMUX_VERSION:-}" || -d /system ]] || die 'Android/Termux non détecté.'
[[ "$(id -u)" != 0 ]] || die 'Exécutez ce script sans su/root.'
pkg_command="$PREFIX/bin/pkg"
[[ -x "$pkg_command" ]] || die 'Gestionnaire pkg absent.'
arch="$("$PREFIX/bin/dpkg" --print-architecture)"
[[ "$arch" == aarch64 || "$arch" == x86_64 ]] || die 'Android 64 bits requis.'
pkg_options=(); $yes && pkg_options=(-y)

state="$PREFIX/libexec/codex-termux"
mkdir -p "$state/releases" "$state/backups"
mkdir "$state/.install-lock" 2>/dev/null || die "Installation déjà en cours. Après un arrêt brutal, vérifiez les processus puis retirez $state/.install-lock."
work=''
cleanup() {
    status=$?
    trap - EXIT
    if [[ -n "$work" && "$work" == "$state"/.stage.* ]]; then rm -rf -- "$work"; fi
    rmdir "$state/.install-lock" 2>/dev/null || true
    exit "$status"
}
trap cleanup EXIT
work="$(mktemp -d "$state/.stage.XXXXXX")"

log 'Préparation des outils Termux officiels'
"$pkg_command" install "${pkg_options[@]}" coreutils tar jq binutils ca-certificates
if [[ -n "$url" ]]; then
    "$pkg_command" install "${pkg_options[@]}" curl
    archive="$work/download.tar.gz"
    curl -fSL --proto '=https' --proto-redir '=https' --retry 3 \
        --connect-timeout 10 --max-time 600 "$url" -o "$archive"
else
    [[ -f "$archive" ]] || die "Archive absente : $archive"
    if [[ -z "$expected" && -f "$archive.sha256" ]]; then
        expected="$(awk 'NR == 1 {print $1}' "$archive.sha256")"
        [[ -n "$expected" ]] || die 'Fichier d’empreinte vide.'
    fi
fi
# Copier dans la zone privée : contrôler et extraire les mêmes octets, même
# si l’archive d’origine se trouve dans Downloads ou est remplacée en parallèle.
cp -- "$archive" "$work/input.tar.gz"
archive="$work/input.tar.gz"
if [[ -n "$expected" ]]; then
    [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die 'SHA-256 invalide.'
    actual="$(sha256sum "$archive" | awk '{print $1}')"
    [[ "$actual" == "${expected,,}" ]] || die 'Empreinte de l’archive incorrecte.'
fi

# Contrôler les chemins et les types avant extraction. Aucun lien ou fichier
# spécial accepté ; seuls le binaire et les métadonnées/licences sont autorisés.
tar -tzf "$archive" > "$work/files"
[[ -s "$work/files" ]] || die 'Archive vide.'
while IFS= read -r name; do
    [[ "$name" =~ ^[A-Za-z0-9_./-]+$ && "$name" != /* ]] || die 'Nom invalide dans l’archive.'
    [[ "/$name/" != */../* && "/$name/" != */./* ]] || die 'Chemin relatif dangereux.'
    case "$name" in bin/|bin/codex|bin/codex-code-mode-host|manifest.json|BUILD.json|licenses/|licenses/*) :;; *) die "Fichier inattendu : $name";; esac
done < "$work/files"
tar -tvzf "$archive" > "$work/types"
awk 'substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" {exit 1}' "$work/types" || die 'Lien ou fichier spécial interdit.'
[[ "$(sort "$work/files" | uniq -d | wc -l)" == 0 ]] || die 'Entrées dupliquées dans l’archive.'
mkdir "$work/payload"
tar --extract --gzip --file="$archive" --directory="$work/payload" --no-same-owner --no-same-permissions
manifest="$work/payload/manifest.json"
candidate="$work/payload/bin/codex"
[[ -f "$manifest" && -f "$candidate" && -f "$work/payload/BUILD.json" ]] || die 'Livraison incomplète.'
jq -e '
    .format == 1 and (.version | type == "string" and test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))
    and (.architecture == "aarch64" or .architecture == "x86_64")
    and (.prefix | type == "string")
    and (.minimum_api | type == "number" and . >= 1 and floor == .)
    and (.binary_sha256 | type == "string" and test("^[0-9a-f]{64}$"))
    and ((.companions // {}) | type == "object")
    and all((.companions // {}) | to_entries[];
        .key == "codex-code-mode-host" and (.value | type == "string" and test("^[0-9a-f]{64}$")))
    and (.dependencies | type == "array")
    and all(.dependencies[];
        (.name | type == "string" and test("^[a-z0-9][a-z0-9+.-]*$"))
        and (.operator == "" or .operator == ">=" or .operator == "<=" or .operator == "=" or .operator == ">>" or .operator == "<<")
        and (.version | type == "string" and test("^[A-Za-z0-9.+:~_-]*$"))
        and ((.operator == "" and .version == "") or (.operator != "" and .version != "")))
' "$manifest" >/dev/null || die 'Manifeste invalide.'
[[ "$(jq -r .architecture "$manifest")" == "$arch" ]] || die 'Archive prévue pour une autre architecture.'
[[ "$(jq -r .prefix "$manifest")" == "$PREFIX" ]] || die 'Archive prévue pour un autre préfixe Termux.'
android_api="$(getprop ro.build.version.sdk)"
[[ "$android_api" =~ ^[0-9]+$ ]] || die 'Version API Android indéterminée.'
((android_api >= $(jq -r .minimum_api "$manifest"))) || die 'Version Android trop ancienne.'
binary_sha="$(jq -r .binary_sha256 "$manifest")"
[[ "$(sha256sum "$candidate" | awk '{print $1}')" == "$binary_sha" ]] || die 'Empreinte du binaire incorrecte.'
[[ "$(od -An -tx1 -N6 "$candidate" | tr -d ' \n')" == 7f454c460201 ]] || die 'ELF 64 bits little-endian requis.'
machine="$(od -An -tu2 -j18 -N2 "$candidate" | tr -d ' \n')"
[[ "$arch:$machine" == aarch64:183 || "$arch:$machine" == x86_64:62 ]] || die 'La cible ELF diffère du manifeste.'
interpreter="$(LC_ALL=C readelf -l "$candidate" | sed -n 's/^.*Requesting program interpreter: \([^]]*\)].*$/\1/p')"
[[ -z "$interpreter" || "$interpreter" == /system/bin/linker64 ]] || die 'Le binaire utilise un chargeur ELF Linux ; une livraison Android est requise.'
LC_ALL=C readelf -h "$candidate" | grep -Eq 'Type:.*DYN' || die 'Un exécutable PIE est requis pour Android.'
companion="$work/payload/bin/codex-code-mode-host"
companion_sha="$(jq -r '.companions["codex-code-mode-host"] // empty' "$manifest")"
if [[ -n "$companion_sha" || -e "$companion" ]]; then
    [[ -n "$companion_sha" && -f "$companion" ]] || die 'Compagnon absent ou non déclaré.'
    [[ "$(sha256sum "$companion" | awk '{print $1}')" == "$companion_sha" ]] || die 'Empreinte du compagnon incorrecte.'
    [[ "$(od -An -tx1 -N6 "$companion" | tr -d ' \n')" == 7f454c460201 ]] || die 'Compagnon ELF invalide.'
    machine="$(od -An -tu2 -j18 -N2 "$companion" | tr -d ' \n')"
    [[ "$arch:$machine" == aarch64:183 || "$arch:$machine" == x86_64:62 ]] || die 'Architecture du compagnon incorrecte.'
    interpreter="$(LC_ALL=C readelf -l "$companion" | sed -n 's/^.*Requesting program interpreter: \([^]]*\)].*$/\1/p')"
    [[ -z "$interpreter" || "$interpreter" == /system/bin/linker64 ]] || die 'Chargeur du compagnon non Android.'
    LC_ALL=C readelf -h "$companion" | grep -Eq 'Type:.*DYN' || die 'Compagnon PIE requis.'
fi

bin="$PREFIX/bin/codex"
if [[ -e "$bin" || -L "$bin" ]]; then
    link="$(readlink "$bin" || true)"
    if [[ "$link" != "$state/releases/"* ]]; then
        owner="$("$PREFIX/bin/dpkg-query" -S "$bin" 2>/dev/null || true)"
        [[ -z "$owner" ]] || die "Codex appartient à un paquet APT ($owner). Désinstallez ce paquet avant de remplacer son binaire."
        $replace || die 'Une installation manuelle existe ; utilisez --replace pour la sauvegarder et la remplacer.'
    fi
fi

mapfile -t dependencies < <(jq -r '.dependencies[].name' "$manifest")
log 'Installation des dépendances depuis vos dépôts Termux configurés'
"$pkg_command" install "${pkg_options[@]}" "${dependencies[@]}" git ripgrep
while IFS= read -r requirement; do
    name="$(jq -r .name <<< "$requirement")"
    operator="$(jq -r .operator <<< "$requirement")"
    required="$(jq -r .version <<< "$requirement")"
    [[ -n "$operator" ]] || continue
    installed="$("$PREFIX/bin/dpkg-query" -W -f='${Version}' "$name")"
    case "$operator" in '>=' ) compare=ge;; '<=' ) compare=le;; '=' ) compare=eq;; '>>' ) compare=gt;; '<<' ) compare=lt;; esac
    "$PREFIX/bin/dpkg" --compare-versions "$installed" "$compare" "$required" || die "Dépendance insuffisante : $name $operator $required (installée : $installed)."
done < <(jq -c '.dependencies[]' "$manifest")

version="$(jq -r .version "$manifest")"
chmod 755 "$candidate"
log 'Vérification du démarrage Android'
if ! version_output="$(timeout 30 "$candidate" --version 2>&1)"; then
    printf '%s\n' "$version_output" >&2
    die 'Le binaire ne démarre pas ; la commande Codex existante reste en place.'
fi
[[ "$version_output" == *"codex-cli $version"* ]] || die 'Version exécutée différente du manifeste.'
timeout 30 "$candidate" --help >/dev/null || die 'Le test --help échoue.'
if [[ -n "$companion_sha" ]]; then
    chmod 755 "$companion"
    timeout 30 "$companion" --help >/dev/null || die 'Le compagnon Code Mode ne démarre pas.'
fi
identity="$(jq -cS '{binary_sha256, companions:(.companions // {})}' "$manifest" | sha256sum | awk '{print $1}')"
target="$state/releases/$version-$arch-${identity:0:12}"
if [[ -d "$target" ]]; then
    [[ "$(sha256sum "$target/bin/codex" | awk '{print $1}')" == "$binary_sha" ]] || die 'Une installation de même identité a été altérée.'
    if [[ -n "$companion_sha" ]]; then
        [[ "$(sha256sum "$target/bin/codex-code-mode-host" | awk '{print $1}')" == "$companion_sha" ]] || die 'Le compagnon installé a été altéré.'
    fi
else
    mv -- "$work/payload" "$target"
fi
if [[ -e "$bin" || -L "$bin" ]]; then
    link="$(readlink "$bin" || true)"
    if [[ "$link" != "$state/releases/"* ]]; then
        backup="$state/backups/$(date +%Y%m%dT%H%M%S)-$$-codex"
        cp -a -- "$bin" "$backup"
        printf 'Ancienne commande sauvegardée : %s\n' "$backup"
    fi
fi
ln -s "$target/bin/codex" "$work/codex-link"
mv -Tf -- "$work/codex-link" "$bin"
printf '\nInstallé : %s\nCommande : %s\n' "$version_output" "$bin"
printf 'Connexion : %q login --device-auth\n' "$bin"
printf 'Lancement : %q\n' "$bin"
visible="$(command -v codex || true)"
if [[ "$visible" != "$bin" ]]; then
    printf 'Une autre commande apparaît avant dans PATH : %s. Utilisez le chemin ci-dessus.\n' "${visible:-aucune}"
fi
