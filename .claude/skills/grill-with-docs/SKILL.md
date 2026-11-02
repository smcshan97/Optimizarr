---
name: grill-with-docs
description: Relentless interview that also writes the glossary and ADRs — use before building a new Optimizarr feature or changing a policy (encode rules, queue behaviour, upscaling, settings) when the goal, edge cases or terms aren't pinned down yet.
---
<!-- From github.com/mattpocock/skills (MIT, see ../LICENSE-mattpocock-skills.txt), adapted for Optimizarr. -->

Call the Skill tool twice, for "anthropic-skills:grilling" and "domain-modeling".

In this environment the grilling skill is `anthropic-skills:grilling`. Skip the interview when the request is already concrete (a named bug, a one-line change) — grill only where a wrong guess would cost real rework.
