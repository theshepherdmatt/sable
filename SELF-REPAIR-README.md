# Fixing Sable Yourself, With an AI Coding Assistant

If something on your Sable box isn't working right (a button doesn't do
anything, the screen stays blank, the rotary knob has stopped scrolling,
the spectrum display is dead, and so on), you can often get it fixed
without waiting on anyone else -- by pointing an AI coding assistant at this
project folder and describing the problem in your own words.

Here's how.

## 1. Connect to the project folder

This project lives on a shared network folder (Samba). On your computer:

- **Windows:** open File Explorer, type `\\192.168.0.105\sable\` into the
  address bar (or "Map network drive" if you want it to stay connected).
- **Mac:** in Finder, press `Cmd+K`, then enter `smb://192.168.0.105/sable`.

You should see folders like `src`, `docs`, `config`, and files like
`README.md`.

## 2. Open your AI coding assistant and point it here

Open whichever AI coding tool you use (for example, Claude Code) and tell
it to work in this folder -- e.g. `\\192.168.0.105\sable\` (Windows) or
`/Volumes/sable/` (Mac, once mounted).

Then tell it something like:

> "Please read the files in `docs/ai-repair/` first, then help me fix a
> problem with my Sable box."

The `docs/ai-repair/` folder contains notes written specifically to help an
AI assistant understand this project quickly -- what the hardware is, which
files matter, common problems and their fixes, and (importantly) what it
should and shouldn't touch.

## 3. Describe your problem in plain English

You don't need any technical terms. Just say what you're seeing, for
example:

- "The rotary knob doesn't scroll the menu any more since I changed my DAC."
- "The screen stays black when I turn the Pi on, but a restart fixes it."
- "Holding the power button doesn't turn the Pi off."
- "The spectrum/visualiser bars don't move even though music is playing."

The assistant should be able to look through the code, figure out what's
likely wrong, and make the fix directly in these files.

## 4. Let it make the change, then reboot

Once the assistant tells you it's made a change, it should ask you to
**reboot your Raspberry Pi**. This is expected and important -- Sable is
set up so that fixes are applied by rebooting the Pi, not by the assistant
restarting things behind the scenes. Just power-cycle the Pi (or run `sudo
reboot` if you're comfortable with that) and check if the problem is fixed.

If it isn't fixed, just go back to the assistant, tell it what you're still
seeing, and let it keep investigating.

## A couple of notes

- It's completely fine if the assistant asks you clarifying questions
  ("which button?", "does the light on the button flash at all?") -- answer
  as best you can.
- If the assistant says it's found the likely cause but isn't confident the
  fix is safe, that's a good sign, not a bad one -- it means it's being
  careful. You can ask it to explain what it found either way.
- Nothing it does should touch your Wi-Fi, your music library, or Volumio/
  moOde itself -- only this Sable project's own files.
