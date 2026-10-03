# Vérifications locales du portage 0.160.0

Préparation du 3 octobre 2026. Cible : Codex CLI 0.160.0, V8 150.4.0, crate cc 1.2.55, toolchain amont 1.95.0.

- Syntaxe Bash des quatre scripts contrôlée avec bash -n.
- Les cinq patches actifs ont été appliqués avec patch --batch --fuzz=0 aux fichiers amont exacts : rust-lang/cc-rs tag cc-v1.2.55, denoland/rusty_v8 tag v150.4.0 et denoland/chromium_build commit 8acb33ac8dceef0503443109c0a92988189563ef. Certains hunks ont un décalage de lignes ; aucun contexte n’a été ignoré.
- 30 tests locaux exécutés avec uv : tous réussis.
- Exécution du point d’entrée complet du conteneur avec le vrai uv : la vérification Python passe et la commande de build est atteinte. Seuls le chemin du montage et le compilateur final sont adaptés à la fixture.
- Contrôles du profil, du tag, du workspace, des versions V8/cc, de la toolchain et d’un changement d’empreinte source.
- Création/extraction de véritables .deb de démonstration, production d’archives 0.122.0 et 0.160.0 avec contrôle de version, du manifeste et du compagnon Code Mode.
- Exécution du hook d’installation sur une arborescence de démonstration : deux binaires, documentation et licences à leurs emplacements.
- Installation et relance avec commandes Termux simulées ; sauvegarde d’une commande manuelle et refus d’écraser un fichier géré par APT.
- Refus de SHA-256 incorrect, d’autre architecture, de chargeur Linux, d’API Android trop ancienne, de chemins dangereux et de dépendances insuffisantes.
- Contrôles du compagnon et conservation de la commande existante lors d’un échec simulé de démarrage.

Les ELF sont des programmes de démonstration produits par GCC. Les commandes et probes Android sont simulées. Le dpkg-deb Linux émet un avertissement sur le nom x86_64 utilisé par Termux ; ces paquets de démonstration sont acceptés.

**Non effectué localement :** compilation complète de Codex/V8 avec Docker/NDK, installation sur téléphone, connexion et session interactive Android. Les logs GitHub Actions déterminent séparément si le build distant a réussi. Les tests locaux ne signifient pas que ce build est déjà validé.
