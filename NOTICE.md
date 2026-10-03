# Provenance et adaptations

Le build actif vise [OpenAI Codex, tag rust-v0.160.0](https://github.com/openai/codex/releases/tag/rust-v0.160.0). La version du workspace, la toolchain 1.95.0, cc 1.2.55 et v8 150.4.0 sont contrôlés avant compilation. L’empreinte de l’archive officielle est calculée, enregistrée et utilisée par le framework pour vérifier son téléchargement.

La base de portage vient de l’archive fournie `tur-codex-inspection.tar.gz`, dossier `tur/codex` du dépôt <https://github.com/termux-user-repository/tur>, commit `9dbeb568b45019614c6b2a57095e2dde735a049f`. Cette recette historique et ses neuf patches sont conservés dans `legacy/tur-0.122.0/codex/`, avec leurs en-têtes et notices.

Le build actif conserve les deux patches cc, le patch sysroot et le patch bindgen. Ce dernier utilise `os.environ.pop(..., None)`. Le patch de sélection des archives et bindings V8 par cible est réécrit pour rusty_v8 150.4.0.

Les cinq patches actifs ont été appliqués aux fichiers amont exacts : cc tag `cc-v1.2.55`, rusty_v8 tag `v150.4.0` et sous-module Chromium au commit `8acb33ac8dceef0503443109c0a92988189563ef`. Le sous-module V8 du tag est `ac1e23989121713ca642f6650b34deff7b686896`.

Le patch CPAL du TUI est retiré après réorganisation de l’audio en amont ; les dépendances CPAL actuelles du voice host excluent déjà Android. Les trois patches jumbo sont retirés du build actif, qui n’active pas ce mode.

V8 est compilé avec `v8_enable_sandbox`, activant également la compression de pointeurs, comme la dépendance du runtime Code Mode de 0.160.0. `codex-code-mode-host` est livré avec `codex`.

Ce projet ajoute les scripts, métadonnées, tests et workflow. Il suit la toolchain amont exacte, verrouille Cargo, supprime le raccourci historique `.built` et utilise le framework officiel <https://github.com/termux/termux-packages>. Aucun dépôt TUR n’est activé et aucun paquet Codex TUR n’est téléchargé.

La recette copie explicitement LICENSE et NOTICE de Codex et les fichiers LICENSE* présents aux racines de rusty_v8 et de son sous-module V8. L’assembleur les conserve dans la livraison. Les licences tierces restent celles des projets concernés ; aucune réattribution des patches d’origine n’est revendiquée.
