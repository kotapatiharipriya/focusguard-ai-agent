# Roadmap

## Now: personal use (done)
Extension with site rules, blocking, Focus page, timer, report. Local agent with loop detection, nudges, Focus Rescue, weekly coach, tray icon, start at login.

## Next: let other people use it

The extension already works without the Python agent, which is what makes this possible.

1. **Chrome Web Store listing.**
   - Write a privacy policy page. The truthful one-liner: no data leaves the device.
   - Add store screenshots (Focus page, report, settings).
   - Narrow permissions where possible.
   - The `webNavigation` and `tabs` permissions will be questioned in review. Explain that they're used to redirect blocked sites.
2. **Onboarding.** On install, a 3-step flow: pick your worst sites, add your first task, try a 1-minute focus session.
3. **Streaks and a weekly email-free summary.** Show a streak of days under your goal on the report.
4. **Firefox build.** It's mostly the same code; `browser.*` namespaces and MV3 differences are small.
5. **Agent packaging.** Ship the agent as a signed app (PyInstaller or Briefcase) so nobody needs a terminal. This only matters for the loop-detection and coach features.

## Phase 3: phone

The scrolling that matters most happens on the phone, but phones don't allow browser-style extensions. The approach differs by platform.

**Android (most feasible)**
- Use `UsageStatsManager` for time per app, and an `AccessibilityService` to recognize when you're inside Shorts or Reels specifically. Both need permission prompts the user grants once.
- Blocking means drawing an overlay that opens a FocusGuard AI Focus screen with the same tasks and timer.
- Reuse the same rules model (off/track/limit/block), day stats shape and coach. They're platform-neutral JSON.
- Build in Kotlin, or Flutter if you want iOS later from the same UI code.

**iOS**
- Apple's Screen Time APIs are the only way: `FamilyControls` to pick apps, `ManagedSettings` to shield them, and `DeviceActivity` for schedules and usage thresholds.
- They need the Family Controls entitlement, which Apple grants on request.
- iOS doesn't let apps see inside other apps, so "Shorts vs the rest of YouTube" isn't possible there. Shields apply per app or per website.

**Sync between devices**
- Keep it private: sync over the local network to the agent on your computer, or use end-to-end encrypted sync so the server only sees ciphertext.
- Then the report and coach can cover phone + laptop together.

## Ideas worth trying
- **Calendar-aware focus.** Auto-start a focus session for calendar blocks named "Deep work".
- **"Why did I open this?"** A one-tap reason picker (bored, avoiding something, tired, habit) on the Focus page, fed to the coach. It would help explain triggers.
- **Commitment mode.** Raising a limit only takes effect tomorrow, so in-the-moment you can't negotiate with yourself.
- **Task import** from Todoist or Notion.
