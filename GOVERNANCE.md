# Governance

UniEnv AI is a young, community-driven project. Governance is kept deliberately light and will grow with the community.

## Roles

- **Maintainer**: [@rupeshbharambe24](https://github.com/rupeshbharambe24) (project founder). Reviews and merges PRs, cuts releases, and makes final calls when consensus isn't reached.
- **Recipe owners**: whoever contributes a battery can be listed in `maintainers` in the recipe and in `CODEOWNERS` for that file. They are asked to review changes to it.
- **Contributors**: everyone who opens issues, discussions or PRs.

Active contributors with sustained, high-quality contributions will be invited to become maintainers.

## Decisions

- Day-to-day changes (recipes, fixes, docs) are decided in PR review.
- Changes to the **recipe schema**, the files UniEnv writes outside `~/.unienv`, or the security model need a short proposal in a GitHub Discussion (category "Ideas") and at least one week for comments before merging.

## Principles

1. **Vendor-neutral.** UniEnv works the same for every AI tool and is not tied to any one AI company.
2. **Never surprise the user.** Everything written outside `~/.unienv` is inside marked blocks and removable with `unienv uninstall`.
3. **Declarative recipes.** Recipes name packages; they never run arbitrary install scripts.
4. **Thin glue over durable tools.** Homebrew, uv, npm and the OS package managers do the installing.
