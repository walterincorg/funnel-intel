## VPS / Deployment

- **VPS host:** `187.124.241.54` (Hostinger Ubuntu, SSH as root)
- **Code path on VPS:** `/opt/funnel-intel`
- **Dashboard:** loopback-only on port 4318, access via `ssh -L 4318:127.0.0.1:4318 root@187.124.241.54`
- **Services:** `funnel-dashboard`, `funnel-worker@{1,2,3}`, `openclaw-gateway`
- **DB:** Supabase (remote, shared between local and VPS)
- **Deploy:** push to main, then `ssh root@187.124.241.54 "cd /opt/funnel-intel && git pull && systemctl restart funnel-dashboard funnel-worker@1 funnel-worker@2 funnel-worker@3"`

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
