# Changelog

All notable changes to this project will be documented in this file.

## [v0.3.0](https://github.com/somaz94/claude-plugins/compare/v0.2.1...v0.3.0) (2026-08-04)

### Bug Fixes

- stop assuming translation mirrors that most users do not keep ([a2cb153](https://github.com/somaz94/claude-plugins/commit/a2cb1536188e1049f812a1fb37912da02e285cf8))

### Documentation

- link the Korean census README from the marketplace README ([8bc4790](https://github.com/somaz94/claude-plugins/commit/8bc4790778e7dea97f5171fc58962e1529e6e5f4))
- fix awkward Korean in the census README translation ([4d31f2d](https://github.com/somaz94/claude-plugins/commit/4d31f2db877e6cfa2289da0117ae057c1450fe2f))
- add a usage walkthrough and a Korean README for census ([ba482a3](https://github.com/somaz94/claude-plugins/commit/ba482a344510f4040d389063831e2d0286a17e93))

### Tests

- cover inline-shell hooks and malformed config shapes ([47361e8](https://github.com/somaz94/claude-plugins/commit/47361e888714cd6a396369c0b6cf52bf11cf507a))

### Chores

- release census 0.3.0 ([b2f02e1](https://github.com/somaz94/claude-plugins/commit/b2f02e1827e74bb10415d6ccbbe34dd6fd81d9f6))

### Contributors

- somaz

<br/>

## [v0.2.1](https://github.com/somaz94/claude-plugins/compare/v0.2.0...v0.2.1) (2026-08-04)

### Bug Fixes

- survive a malformed settings.json instead of aborting the audit ([e6cdd57](https://github.com/somaz94/claude-plugins/commit/e6cdd57dfc44d010de03bb054cdf3509dff6b455))
- stop reporting inline-shell hooks as missing scripts ([ac2f7bc](https://github.com/somaz94/claude-plugins/commit/ac2f7bc2adea469eda368ce6d19c2ca9d51cf7f8))

### Chores

- release census 0.2.1 ([34bdfb9](https://github.com/somaz94/claude-plugins/commit/34bdfb929b012c69c12d6d7a7a98ddb6feae6d5e))

### Contributors

- somaz

<br/>

## [v0.2.0](https://github.com/somaz94/claude-plugins/compare/v0.1.1...v0.2.0) (2026-08-03)

### Features

- detect a translation mirror its source has moved past ([1fa121a](https://github.com/somaz94/claude-plugins/commit/1fa121a8ed4b02dc2ac9d5919132711434d80325))
- audit hooks through the script they run ([537d770](https://github.com/somaz94/claude-plugins/commit/537d770e0d967aef8579b8108827d436dd81e0e1))

### Bug Fixes

- grade an item against the name of the repo it lives in ([90d70f7](https://github.com/somaz94/claude-plugins/commit/90d70f754fc3165aefc9e72215629035d6ec79b2))
- report context cost per session and guard the --out target ([e18afc1](https://github.com/somaz94/claude-plugins/commit/e18afc184b0efa625bfe8e4afbf00b3e44858c46))

### Continuous Integration

- assert a stale mirror is caught by history ([94e2c0d](https://github.com/somaz94/claude-plugins/commit/94e2c0d70824c90bcea394be4652e403e0cf45c0))
- assert hooks are read through their target script ([774e677](https://github.com/somaz94/claude-plugins/commit/774e677355c074d182c7dc02adad36404d84b0f1))
- assert repo-scoped coupling is detected ([255e39e](https://github.com/somaz94/claude-plugins/commit/255e39ec9c71ea27e1a65537059bd29e5082c5ef))
- run on version tags and cover the new census guards ([4c868d7](https://github.com/somaz94/claude-plugins/commit/4c868d7896fab8165f34e390e6e88d130266d5c9))

### Chores

- release census 0.2.0 ([5b93442](https://github.com/somaz94/claude-plugins/commit/5b934422d497c59c6ddbd0430ffb4c1d19dacb18))

### Contributors

- somaz

<br/>

## [v0.1.1](https://github.com/somaz94/claude-plugins/compare/v0.1.0...v0.1.1) (2026-08-03)

### Bug Fixes

- resolve plugin source without metadata.pluginRoot ([16a1610](https://github.com/somaz94/claude-plugins/commit/16a161044987148e61b993ebec5fa2532044ed35))

### Contributors

- somaz

<br/>

## [v0.1.0](https://github.com/somaz94/claude-plugins/releases/tag/v0.1.0) (2026-08-03)

### Features

- add drift skill with per-directory mirror calibration ([ea3534d](https://github.com/somaz94/claude-plugins/commit/ea3534d3cba7a139a480e6db667b075c8423bfc7))
- add portability skill grading items by machine-specific coupling ([92c2076](https://github.com/somaz94/claude-plugins/commit/92c207657274f6d35f6c800ab1ba954b8d8ed852))
- add census plugin with config catalog and context-budget report ([6e1a31a](https://github.com/somaz94/claude-plugins/commit/6e1a31aff7cc06f12a560a4baa209ed6bb862947))

### Documentation

- add marketplace and census READMEs, accept --config after subcommand ([ea969fb](https://github.com/somaz94/claude-plugins/commit/ea969fb7ad54d75d156e42f7ced1045f805eec05))

### Continuous Integration

- add workflow scaffold, release automation, and plugin validation ([046e411](https://github.com/somaz94/claude-plugins/commit/046e41101af6c77fb7e064863b57437c90bc0984))

### Chores

- reset census to 0.1.0 for the first release ([3ba7917](https://github.com/somaz94/claude-plugins/commit/3ba7917f4016420dd3522d794c52b83c90ef91bb))

### Contributors

- somaz

<br/>

