# Storage and cleanup

Read before moving, copying, archiving or deleting anything, and before any step that writes large files.

## Rules

- **STO-01** Keep one copy and clear the rest. The long-term drive holds the only original. The working machine holds masters, proxies and the work folder, and nothing else: no stabilised, no-grade, second-render or phone copies. Once a batch has posted and its b-roll and grade are checked, those are cleared by script (STO-06). A master is remade from the raw footage and the saved look if it is needed again.
- **STO-02** Every delete goes through the Trash, moved by script and emptied by the human, never `rm`. A trimmed original goes to the Trash only after every trimmed part verifies. A clip the human cuts is moved, never deleted.
- **STO-03** Move, never delete. Setup keeps a timestamped `.bak` of anything it replaces and moves clashing folders to an archive folder.
- **STO-04** Check free space before copying or starting a batch of renders. A nearly full disk makes cloud sync evict files (FOO-16). A same-volume move needs no space. When space is short, ask the user what to clear.
- **STO-05** A script never touches what it did not make. Export removes only files listed in its own manifest and leaves hand-staged folders alone. Transient render folders are temp directories removed on exit, success or failure. (judgment)

## What can leave

- **STO-06** Plan, then go. A cleanup first writes a plan that changes nothing: what would go to the Trash with sizes, what would be trimmed into which parts, and any clip an edit plan still names. It runs only on the user's go, runs exactly the saved plan, and skips any file that changed since the plan was made.
- **STO-07** A cleanup of intermediates never touches camera originals or finished videos. Originals, the finished videos in the hand-off folder and anything the cleanup did not make stay put. The only way an original leaves is the keep and cut apply step, after every trimmed part verifies and on the user's go (STO-02).
- **STO-08** Every cleanup writes a receipt: what moved to the Trash, how big it was, when, and which saved plan it ran. The receipt stays with the batch, and cleanup never deletes it.
- **STO-09** A report-only what-can-leave sizing lists render intermediates by age and posted status. These are per-video cut files, mezzanines, word-timing and transcript caches, no-grade and face-locked copies, stills, repost variants, conformed b-roll, and grade and logo caches. It changes nothing and feeds the plan in STO-06. It never lists originals or finished videos.
- **STO-10** The plan-then-go Trash step is the only deleter. Nothing else in the package deletes outside its own temp directories and the files in its own manifest. Space returns only when the user empties the Trash.
