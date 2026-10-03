# Codex 0.160.0 natif pour Termux

Build Android de [Codex CLI 0.160.0](https://github.com/openai/codex/releases/tag/rust-v0.160.0), à partir des sources OpenAI, avec un installateur autonome. Aucun dépôt TUR n’est ajouté et aucun paquet Codex TUR n’est téléchargé.

Le tag `rust-v0.160.0` désigne **Codex**. La toolchain du compilateur Rust déclarée par ces sources est **1.95.0** ; ce sont deux versions distinctes.

**Validation actuelle :** 30 tests locaux réussis. Les cinq patches actifs ont été appliqués sans fuzz aux fichiers amont exacts de `cc 1.2.55`, `rusty_v8 150.4.0` et Chromium `8acb33ac`. Le build Android complet et l’exécution sur téléphone restent à valider. Un artefact binaire n’est disponible qu’après un build réussi.

## Compiler avec GitHub Actions

Le workflow [Build Codex Android](https://github.com/azman0101/codex/actions/workflows/build-android.yml) démarre lors d’une modification du code sur `main`. Il peut aussi être lancé avec **Actions → Build Codex Android → Run workflow**.

La version par défaut est `0.160.0` et l’architecture `aarch64`. `x86_64` peut être choisi pour un appareil correspondant ; vérifier avec `dpkg --print-architecture` dans Termux.

Après réussite, télécharger l’artefact `codex-termux-0.160.0-aarch64`. Son ZIP contient :

```text
codex-termux-0.160.0-aarch64.tar.gz
codex-termux-0.160.0-aarch64.tar.gz.sha256
install-codex-termux.sh
```

Les métadonnées disponibles, y compris en cas d’échec, sont livrées dans un autre artefact. Aucun secret GitHub n’est requis par ce workflow. Les logs permettent de distinguer un manque de ressources du runner d’un problème de portage.

## Installer dans Termux

Décompresser le ZIP et placer ses trois fichiers dans un même dossier accessible depuis Termux. Depuis ce dossier :

```bash
bash install-codex-termux.sh \
  --archive codex-termux-0.160.0-aarch64.tar.gz \
  --yes
codex --version
codex login --device-auth
codex
```

L’installateur vérifie le fichier SHA-256 voisin, les chemins et types d’entrées, le manifeste, les empreintes des exécutables, l’architecture ELF, le chargeur Android, l’API minimale et les dépendances. Il teste le démarrage de Codex et du compagnon avant remplacement de la commande.

`--yes` accepte les dépendances avec `pkg`. Les dépôts déjà configurés sont utilisés ; aucun n’est ajouté. Le préfixe attendu est `/data/data/com.termux/files/usr`. Les architectures 32 bits sont exclues.

Une commande manuelle existante demande `--replace` et est sauvegardée. Un fichier appartenant à APT est refusé, même avec cette option : désinstaller explicitement son paquet avant remplacement. Les releases et sauvegardes restent sous `$PREFIX/libexec/codex-termux/`. Un contrôle échoué conserve la commande existante ; des dépendances peuvent déjà avoir été installées.

`codex-code-mode-host` est livré à côté de Codex dans le dossier de release. Les autres exécutables auxiliaires distincts, notamment le voice host, ne sont pas livrés. Connexion, sessions interactives, outils, Code Mode et sandbox demandent encore des essais réels sur Android. Le script ne désactive pas automatiquement le sandbox.

## Compiler sur Linux x86_64

Installer Git, curl, patch, `sha256sum`, `dpkg-deb`, Docker fonctionnel et [uv](https://docs.astral.sh/uv/getting-started/installation/). Puis :

```bash
git clone https://github.com/azman0101/codex.git
cd codex
bash build-linux.sh aarch64 0.160.0
```

Docker sert à la compilation sur Linux. Le résultat s’exécute directement dans Termux.

Avant Docker, le script contrôle la release, la version inscrite dans les sources, V8, cc et la toolchain. Il remplit l’empreinte source dans une **copie** de la recette ; le framework Termux vérifie cette empreinte lors de son téléchargement. Un changement des sources lors d’une relance est refusé.

Les livraisons finales sont dans `dist/`. Le framework, son commit, le digest Docker, le paquet intermédiaire et la toolchain sont conservés dans `.build/0.160.0/` et réutilisés dans ce dossier. Ces choix ne garantissent pas une reproductibilité bit à bit de toutes les dépendances.

Par défaut, la version Rust exacte déclarée par Codex est utilisée. `CODEX_RUST_TOOLCHAIN` permet un remplacement explicite par une version exacte ou une nightly datée ; un canal flottant est refusé.

Le champ de version du workflow ne garantit pas la compatibilité de toutes les versions Codex. `recipe/codex-termux/profile.json` décrit le profil inspecté, **0.160.0**. Une autre version demande l’adaptation et la vérification du profil et des patches ; elle n’est jamais remplacée silencieusement par une version plus ancienne.

## Sources et tests

La recette active applique deux patches cc, un patch de sélection des artefacts V8 par cible, un patch bindgen et un patch sysroot. Les neuf patches historiques de 0.122.0 sont conservés dans `legacy/tur-0.122.0/codex/` et décrits dans `inspection.md`.

L’ancien patch CPAL du TUI est retiré : l’audio a été réorganisé en amont, et CPAL du voice host exclut déjà Android. Les trois patches jumbo sont retirés du build actif, qui n’active pas ce mode. V8 est construit avec `v8_enable_sandbox`, comme la dépendance Code Mode de 0.160.0.

`BUILD.json` enregistre les versions et empreintes utilisées ; `NOTICE.md` donne leur provenance.

Pour les tests sur Linux x86_64, installer GCC, Bash, jq, binutils, tar et dpkg, puis :

```bash
uv run --no-project --python 3.11 tests/test_native.py
```

Les ELF et paquets de démonstration sont réels ; les commandes et probes Android sont simulés. Voir `VALIDATION.md` pour le périmètre exact.
