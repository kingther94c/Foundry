Stop work on the task for this one reply: the conversation is about to be compacted to free context. Everything before this message, except the user's own messages, will be replaced by what you write now, so write the handoff you would need to carry on without losing work. Do not call any tools; reply with the summary only.

Cover, in this order, only what there is something to say about:

1. The task: what the user asked for, with the constraints and preferences they stated.
2. Done so far: what you changed (files and functions by name) and what you found out that later steps depend on. Write down the facts the remaining work needs from files it will not change -- a specification's rules, an interface's signatures -- so those files need not be read again.
3. Checks: the commands you ran to verify the work and what they showed, and which of them must run again after later changes.
4. Current state: what you were in the middle of, and anything left broken or half-done. A plan you keep with update_plan is added after the summary as it was last set; do not copy it.
5. Next steps: what remains, in order.
6. Files for the next step: the files the next step has to open, at most eight, most important first, each with what it is needed for and, where only part of it matters, which part (a function, a section, a line range). Leave out the files whose content this summary already gives and those only finished steps needed: they are not read again unless the work turns out to need them.
7. Decisions and dead ends: approaches you chose or rejected, and why, so they are not tried again.

Name files by their path in the workspace, and quote exact identifiers, error messages and values that matter. Leave out file contents and command output that can be read again. Where an earlier summary is above, keep from it only what the remaining work needs. Stay under about 1,500 words.
