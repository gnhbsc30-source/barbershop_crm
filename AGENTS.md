# Barbershop CRM

Use [project state](docs/PROJECT_STATE.md) for milestone status and the existing STOP rule. Consult [architecture decisions](docs/ARCHITECTURE_DECISIONS.md) for project contracts.

- Use [barbershop-ui-design](.agents/skills/barbershop-ui-design/SKILL.md) for frontend screens, components, booking and OWNER calendar UX, RTL, responsiveness, accessibility, and branding.
- Use [barbershop-security-review](.agents/skills/barbershop-security-review/SKILL.md) for authentication, authorization, tenant resolution, public slugs, customer data exposure, booking and appointment mutations, browser session integration, and security-sensitive architecture.
- Use both when UI work crosses a security boundary. If the client does not select the skill automatically, invoke it explicitly or read its linked instructions.
