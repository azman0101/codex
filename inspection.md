# Inspection du répertoire tur/codex

## Périmètre et résultat

Inspection de tous les fichiers de l'archive fournie, au commit TUR
`9dbeb568b45019614c6b2a57095e2dde735a049f`.

Le répertoire contient une recette de compilation et neuf patches : dix fichiers,
2 156 lignes et 79 919 octets. `COMMIT.txt` est un onzième fichier, extérieur au
répertoire de la recette. Il n'y a ni binaire, ni sources Codex/V8 complètes,
ni framework de compilation Termux dans cette archive.

La recette vise Codex **0.122.0**, révision de paquet **1**, et annonce les
dépendances d'exécution `libc++` et `openssl`. Elle exclut `arm` et `i686`.
Ces informations concernent cet instantané, pas nécessairement le dépôt actuel.

**Résultat principal : TUR compile Codex et rusty_v8 pour une cible Android.
Il ne se contente pas de décompresser un exécutable Linux officiel.** Une
installation autonome peut réutiliser ces adaptations sans activer le dépôt
APT TUR ; elle doit cependant disposer d'un binaire Android construit et validé.
Un changement de nom d'architecture dans un installateur ne reproduit pas cette
chaîne de compilation.

## Inventaire exhaustif

| Fichier | Lignes | Effet observé et conséquence |
|---|---:|---|
| `build.sh` | 184 | Télécharge Codex, adapte des crates, reconstruit rusty_v8 avec le NDK, lie Codex et installe le binaire. Nécessite les fonctions et variables du framework Termux. |
| `0001-disable-cpal.patch` | 298 | Étend à Android les conditions qui désactivent déjà l'audio Linux dans le TUI. Modifie six fichiers Codex. La sélection des périphériques et les fonctions audio locales sont neutralisées ; `RealtimeConversation` est aussi rendu indisponible dans ce TUI Android. |
| `rust-cc-do-not-concatenate-all-the-CFLAGS.diff` | 38 | Dans la crate `cc`, reprend la première variable de flags définie au lieu de concaténer les variables générales et spécialisées. Évite de propager les flags Android aux outils compilés pour l'hôte dans cette chaîne. |
| `rust-cc-allow-warnings.diff` | 10 | Retire `#![deny(warnings)]` de la crate `cc`. C'est un assouplissement de compilation local à cette crate, pas une désactivation générale des avertissements Codex. |
| `rusty-v8-search-files-with-target-suffix.diff` | 42 | Fait lire prioritairement à la crate `v8` les variables `RUSTY_V8_ARCHIVE_<TARGET>` et `RUSTY_V8_SRC_BINDING_PATH_<TARGET>` ; ajoute leur suivi par Cargo. Sépare les artefacts hôte et Android. |
| `v8-patches/0001-unset-BINDGEN_EXTRA_CLANG_ARGS-in-v8_s-bindgen.patch` | 12 | Retire `BINDGEN_EXTRA_CLANG_ARGS` de l'environnement du script bindgen interne à Chromium, afin de ne pas mélanger deux configurations de génération. Le `del` employé échoue si la variable manque. |
| `v8-patches/0002-install-sysroot.patch` | 11 | Ajoute Android à la branche aarch64 qui active et prépare les sysroots `arm64` et `amd64` dans le build rusty_v8. Le sysroot Linux utilisé pour des étapes du build ne signifie pas que le binaire final cible Linux. |
| `v8-patches/0101-reland-jumbo-scripts.patch` | 449 | Réintroduit les templates GN et le script Python qui regroupent plusieurs sources C/C++ dans une unité de compilation. Optimisation de temps de compilation. |
| `v8-patches/0102-reland-jumbo-cflags.patch` | 26 | Ajoute `-Werror=macro-redefined` au bloc des avertissements par défaut. Le flag n'est pas conditionné par `use_jumbo_build` dans ce patch, même si son objectif déclaré est de sécuriser les builds jumbo. |
| `v8-patches/0103-reland-jumbo-for-v8.patch` | 1 086 | Branche V8 sur les templates jumbo, exclut des sources incompatibles et corrige des collisions de macros, noms et types. Le patch réel touche 46 chemins ; son en-tête de statistiques historique n'énumère pas tous les ajouts ultérieurs. |

## Chaîne de compilation reconstruite

1. Le framework télécharge les sources de Codex 0.122.0 et vérifie l'empreinte
   `b012a31ce96076dd2a71a3b9606c8a598952140d896a7b12ec07b1471ed130da`.
2. Le framework applique le patch racine `0001-disable-cpal.patch`. Ce travail
   n'est pas déclenché explicitement dans `build.sh` ; c'est une étape du framework.
3. Dans `codex-rs`, `cargo vendor` récupère les dépendances ; la recette ne conserve
   que les copies de `cc` et `v8`, les patche, puis ajoute leurs chemins dans
   `[patch.crates-io]`.
4. Elle détermine une version de `v8` via `cargo info v8`, clone le tag correspondant
   de `denoland/rusty_v8` et ses sous-modules, puis applique les cinq patches V8
   dans l'ordre des noms.
5. Elle configure GN/Ninja, le niveau API Android, le NDK, les flags bindgen et
   `use_jumbo_build=true`, puis compile rusty_v8 depuis les sources.
6. Elle conserve `librusty_v8.a` et `src_binding.rs` comme artefacts intermédiaires.
   La bibliothèque V8 est liée statiquement à Codex ; cela ne rend pas tout
   l'exécutable Codex autonome vis-à-vis des bibliothèques Android/Termux.
7. Elle compile `codex-cli`, binaire `codex`, avec `cargo +nightly rustc` et une cible
   Android. En aarch64, elle ajoute la bibliothèque trouvée par
   `$CC -print-libgcc-file-name` pour résoudre notamment `__clear_cache`.
8. Elle installe uniquement le binaire `codex` et la documentation. Il n'y a pas
   d'installation explicite de `codex-code-mode-host` ou d'autres exécutables
   compagnons dans cet instantané.

## Ce qui est nécessaire, conditionnel ou optionnel

La cible Android, le NDK, la préparation des bibliothèques et la correction audio
font partie de la stratégie de portage effectivement employée. Les patches
`cc`, bindgen, sysroot et variables V8 répondent à l'organisation particulière
de cette compilation croisée ; leur nécessité doit être réévaluée si cette
organisation change. Ils ne sont pas des patches que l'on applique après avoir
téléchargé un exécutable officiel.

Les trois patches jumbo peuvent être étudiés comme un bloc d'optimisation
optionnel. Un premier portage peut tenter `use_jumbo_build=false` et omettre ce
bloc afin de réduire les modifications de V8. Cela reste une hypothèse à
confirmer par compilation et tests, avec un temps de build potentiellement
supérieur. Ne pas retirer un seul patch jumbo tout en conservant l'activation
et les templates introduits par les autres.

La recette assume un environnement de compilation avec des outils hôte Linux
x86_64, notamment le chemin NDK `prebuilt/linux-x86_64`. Elle ne peut pas être
exécutée telle quelle dans Termux ARM64. Porter aussi toute la chaîne V8/GN et
ses outils hôte au téléphone serait un travail distinct.

## Corrections à prévoir pour une chaîne indépendante

### Reproductibilité

- Remplacer `nightly` flottant par une toolchain précise et testée. Le commentaire
  sur `std::fs::File::lock` ne prouve pas que toutes les toolchains stables
  actuelles conviennent à Android. Les API sont stables depuis Rust 1.89 ;
  l'implémentation Android et les besoins de la version Codex restent à vérifier.
- Lire et verrouiller la version V8 à partir du `Cargo.lock` de Codex, ainsi que
  les commits de sous-modules. `cargo info v8` n'est pas ici une vérification
  explicite de conformité à ce fichier de verrouillage.
- Verrouiller le framework Termux, le NDK, GN/Ninja et les patches. La seule
  empreinte des sources Codex ne verrouille pas toute la compilation.
- Remplacer le témoin `.built` par un cache dont la clé intègre version V8,
  architecture, NDK/API, toolchain et empreintes des patches/flags. Le témoin
  actuel ne contient aucune de ces informations.
- Utiliser `--locked` quand c'est compatible avec les transformations locales,
  ou conserver et documenter le lockfile résultant si les patches le modifient.

### Robustesse

- Séparer `local v8_version` de l'affectation qui lance `cargo info`, puis vérifier
  son statut et que la version n'est pas vide. `local var=$(commande)` peut
  masquer l'échec de la commande.
- Remplacer `del os.environ["BINDGEN_EXTRA_CLANG_ARGS"]` par
  `os.environ.pop("BINDGEN_EXTRA_CLANG_ARGS", None)`.
- Itérer sur les patches sans découpage par espaces (`for f in $(find ...)`).
- Appliquer les patches avec un échec explicite, conserver leurs logs et refuser
  un mélange de patches périmés avec une nouvelle release Codex/V8.
- Limiter la portée des variables GN, NDK et bindgen à la compilation V8 ; vérifier
  les artefacts obtenus avant de marquer le cache comme terminé.

### Livraison et validation

La chaîne indépendante doit produire un artefact Android avec manifeste de
version, architecture, niveau API, empreinte et dépendances. L'installateur
Termux pourra utiliser cet artefact sans ajouter TUR aux sources APT.

Il faudra vérifier l'ELF et ses bibliothèques nécessaires, puis tester sur
Android : démarrage, TUI, connexion, création/reprise de session, commandes
shell et restrictions de sandbox. Un `codex --version` réussi ne valide pas
ces comportements. Aucun des neuf patches ne constitue une correction
générale du sandbox Linux pour Android.

Le build n'installe que `codex`. Avant de promettre une équivalence avec une
distribution officielle récente, vérifier les besoins des fonctions qui
utilisent des exécutables compagnons et décider explicitement de leur périmètre.

## Choix technique proposé

Construire d'abord un binaire Android sur Linux x86_64 en réutilisant un framework
Termux verrouillé et une recette locale adaptée. Cela réutilise des outils de
compilation sans installer le paquet TUR ni activer son dépôt sur le téléphone.
Distribuer ensuite ce binaire dans une archive dédiée, installée par un script
Termux autonome avec contrôle d'intégrité et remplacement atomique.

Commencer par la version 0.122.0 correspondant aux patches, ou porter et vérifier
ces patches sur une version Codex explicitement choisie. Ne pas annoncer que le
simple remplacement de `TERMUX_PKG_VERSION` suffit à construire la dernière
release.

## Validation réellement réalisée

- Lecture de tous les dix fichiers du répertoire et du commit joint.
- Syntaxe de `build.sh` vérifiée avec `bash -n`.
- Structure des neuf patches validée avec `git apply --numstat`.
- Analyse statique des effets et des dépendances du framework.

Les sources Codex, les sources V8 complètes et le framework ne figurent pas dans
l'archive : aucune application des patches sur les sources réelles, compilation
NDK ou exécution Android n'a été effectuée. Les validations structurelles ne
prouvent pas qu'une nouvelle version de Codex soit compatible.

## Références complémentaires

- [Framework Termux : étapes et hooks de compilation](https://github.com/termux/termux-packages/wiki/Building-packages)
- [Documentation Rust : File::lock et File::try_lock](https://doc.rust-lang.org/std/fs/struct.File.html)

Les observations sur la recette et les patches proviennent de l'archive fournie,
qui est la source de référence de cette inspection.
