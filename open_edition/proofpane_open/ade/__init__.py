"""The ADE of Proofpane Community, its coordination version.

The owner runs their coding sessions themselves, in Anthropic's or OpenAI's own programs: one in the cloud, one on their
machine. They work on the same repositories and pass work to each other through files on one private branch. The ADE
reads that branch and the repositories and shows the owner, on any screen, what each session did and what it is
waiting for. It starts no program, hosts no terminal, holds no Claude or ChatGPT credential, and merges or deploys
nothing by itself: a session's held commands still wait for the owner's own approval.

Modules:
    channel   a channel directory's files read into logs, windows, questions, answers and instructions
    commits   who made a commit: a cloud session, an agent session on someone's machine, or a person
    sources   where the files and commits come from, a local clone or GitHub's API
    board     a channel, its commits and the time, read into what the owner sees
    conversation  the board's channels as two transcripts, the cloud session's and the local one's
    ledger    the owner's connected repositories, and each commit's signed Verify result
    verifier  Verify on each commit of a connected branch, the manifest signed
    actions   the owner's answers, windows and stops, as texts and composite signatures
    check     the check a session runs before it acts on a file the ADE wrote
    approvals hook tokens and the approval queue; the hook itself is open_edition/ade_hook.py
    routes    the HTTP surface, under /api/ade
"""
