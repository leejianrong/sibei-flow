---
name: natural-writing
description: Write prose that reads like a human wrote it, not an LLM. Use whenever drafting or editing articles, blog posts, docs, or any narrative prose for this project, especially the Tech Blog Pipeline articles. Covers the real AI tells (uniform sentence rhythm, hedging, "not just X but Y", stock vocabulary, em-dash overuse, title-case headings) and how to fix them.
---

# Natural writing

Your default prose has a texture that gives it away as machine-written. This skill
is about removing that texture. The goal isn't to beat AI detectors. It's to write
the way a thoughtful engineer writes, with rhythm and a point of view.

## The one rule that matters most: vary your rhythm

LLM prose settles into a metronome, sentence after sentence in the 18-to-24-word
range. Human prose varies. A short sentence can sit next to a long, winding thought
that keeps unspooling clauses well past the point where a machine would have
stopped. Fix your rhythm and half the other tells fix themselves.

Vary rhythm, but don't manufacture choppiness. A wall of two- and three-word
fragments is its own tell, and it reads as breathless. Even, flowing prose that
joins its clauses with "and" and "but" is a perfectly human register too, often a
calmer and more confident one. The goal is natural variation, not staccato for its
own sake. When in doubt, prefer a complete sentence to a clipped fragment, and let
the long-vs-short contrast come from the ideas rather than from chopping.

Bad, uniform, every sentence the same length:

> Event-driven systems provide a number of benefits for modern applications. They
> allow services to communicate without being tightly coupled together. This makes
> the overall system more resilient to individual component failures. It also
> enables teams to scale different parts of the system independently.

Good, varied, short then long then short:

> Event-driven systems buy you one thing above all: decoupling. A service drops a
> message and moves on, never knowing or caring who picks it up, which means a
> consumer can crash, restart, and catch up later without anyone upstream noticing.
> That's the resilience story. It's also where the hard bugs live.

Read it aloud. If you never once have to catch your breath, the rhythm is too flat.

## Kill these specific tells

The "not just X, but Y" construction. "This isn't just a cache, it's a contract."
LLMs reach for this constantly. Cut it and say the thing directly.

Hedging into the polite middle. "It's worth noting that there are several factors
to consider." Take a position. If you think something is a bad idea, write that
it's a bad idea.

Rule-of-three padding. Not every list wants exactly three items. Real emphasis
often comes in twos, or in one blunt clause.

Stock vocabulary. Delve, leverage, utilize, robust, seamless, foster, landscape,
realm, tapestry, testament, crucial, pivotal, underscore, "in the ever-evolving
world of." Reach for the plain word instead: use, not utilize; helps, not
facilitates.

Hollow openers and closers. Don't open with "In today's fast-paced world." Don't
close with "In conclusion" or "Ultimately, the key takeaway is." Start on the idea
and stop when you're done.

Empty intensifiers. "Very," "really," "incredibly," "a powerful tool that." Delete
them, or swap in a concrete detail that earns the emphasis.

## Em-dashes: the overrated tell

Em-dash overuse is a real signal, but it's the weakest one and the easiest to
overcorrect. Don't ban them; humans use them. Just don't let them become your only
way to set off an aside. Rotate through commas, parentheses, a colon, or a full
stop and a fresh sentence. If a paragraph has three em-dashes, two of them are lazy.

## Formatting tells

Use sentence case for headings, not Title Case. "Designing resilient services," not
"Designing Resilient Services."

Don't over-bullet. If ideas connect, write a paragraph. Bullets earn their place
only when items are genuinely parallel and scannable, not when you've chopped an
argument into fragments to look organized.

Don't bold every other phrase. When emphasis is everywhere it means nothing.

## Specificity is the human fingerprint

Vagueness is the deepest tell, because a real writer knows things. Push every
abstraction toward a concrete detail:

- "a recent study" becomes the study's name, or you drop the appeal to authority
- "many developers" becomes a number, or a specific situation you've watched happen
- "improves performance significantly" becomes "cut p99 latency from 400ms to 90ms"
- "various tools" becomes the actual tool names

Can't be specific? That's usually the sentence telling you it isn't carrying its
weight. Cut it.

## Have a point of view

Machine prose is relentlessly neutral. Good technical writing argues. It says "most
teams reach for Kafka here and regret it," then earns the claim. Take sides, make a
recommendation, own the trade-offs. A reader should finish knowing what *you* think,
not just what the options were.

## Revision pass, before you call any draft done

1. Read it aloud. Wherever you stumble or a phrase feels stiff, rewrite it.
2. Check the rhythm. Three sentences in a row at the same length? Break one, merge
   two, drop in a fragment.
3. Hunt the tells. Search for "not just," "isn't just," "delve," "leverage,"
   "seamless," "robust," "in conclusion," "it's worth noting." Fix every hit.
4. Cut 10%. Almost every draft is padded. Remove the qualifiers, the throat-clearing,
   the sentence that just restates the one before it.
5. Check the surface. Headings in sentence case, lists that aren't smuggling prose,
   bolding used sparingly.

## The core technique: imitate, don't generate

Given a sample of the user's own writing, or a writer they admire, study its rhythm
and diction and match it. Showing a model good writing beats any list of rules. When
in doubt, ask for a paragraph they consider "sounds like me" and use it as your north
star.

## Jian's voice (the profile for this project's articles)

The general rules above are the floor. This section is the target. When writing as
Jian for the Tech Blog Pipeline, match this profile. It was reverse-engineered from
his hand-edits to a draft, so it reflects what he actually does, not what sounds
good in the abstract. It will grow as more articles are edited.

The evidence, with the full before/after diff and the reasoning behind each bullet
below, lives in
[references/case-study-hello-im-jian.md](references/case-study-hello-im-jian.md).
Append each new article's diff there and revise this profile when the evidence
disagrees.

- **Even and flowing, not choppy.** Jian joins clauses with "and" and "but" rather
  than breaking them into short fragments. He consistently rewrites punchy two-word
  sentences ("Both count.", "Never both.", "More soon.") into complete ones ("They're
  both valid approaches.", "There'll be more coming soon."). Do not reach for
  staccato with him. His rhythm still varies, but it's calm.
- **No narrator scaffolding.** He deletes every line that announces the structure of
  the post: "A little background first.", "So why start writing now?", "A quick
  opinion, because...". State the thing directly. Never signpost what you're about
  to say.
- **Understated, never showy.** He cuts quotable aphorisms and motivational
  punchlines ("you're not obsolete, you're early", "a lesson worth stealing"). He
  softens preachy phrasing ("pretending otherwise helps no one" becomes "it's hard
  to pretend otherwise") and swaps dramatic verbs for plain ones ("rot our maths"
  becomes "make kids bad at maths"). Avoid the thought-leader register entirely.
- **Peer, not teacher.** He writes "we" where a lecture would write "you" ("we're
  trying to keep up", "what we slow down for"). He stands alongside the reader as a
  fellow learner. Keep "you" only for direct address, not for dispensing advice.
- **Confident on values, humble on advice.** He states beliefs flatly ("LLMs and
  coding agents are a good thing") but frames predictions and recommendations gently
  ("I think we can still learn", "Hopefully this blog inspires you"). Mild hedging is
  part of his voice here, and it's the one place the general "don't hedge" rule bends
  for him. Never hedge the core belief; feel free to soften the takeaway.
- **Concrete and true beats vivid-but-vague.** He keeps vivid language when it's
  grounded ("flies by", "tear through thousands of lines", "fell for it", "2am") and
  replaces cute filler with real detail ("all the glue in between" became "machine
  learning applications on the side"). Accuracy about his own life matters to him;
  don't invent color.
- **Parentheses for asides.** His default aside punctuation is parentheses ("(one
  function at a time)", "(which engineers are notoriously bad at)"), not dashes.
- **British spelling and conventions.** "learnt", "specialised", "maths". He
  capitalises proper names properly, including his degree ("Electrical and Electronic
  Engineering").

When unsure, err toward plainer, calmer, and more modest. If a line feels clever,
he'll probably cut it.

## Case study: the offenses in this file's first draft

This file used to break its own rules. Keeping the receipts here, because a concrete
before-and-after teaches more than the rules alone, and because it's a reminder that
the first draft is always guilty until edited.

- **Em-dash overuse.** The original leaned on em-dashes in more than a dozen places,
  several paragraphs stacking two or three, in the very document that tells you not
  to. This was the worst offender and the most embarrassing.
- **Title Case heading.** The H1 read "Natural Writing," breaking the sentence-case
  rule three sections below it.
- **Rule-of-three flourishes.** The intro promised "rhythm, specificity, and a point
  of view," a tidy triple assembled for cadence rather than because there were
  exactly three things to say.
- **Bolded bullet leads.** Nearly every bullet opened with a bolded phrase, which is
  the "emphasis everywhere means nothing" smell. (This section keeps the bold leads
  on purpose, as scannable labels for a genuine list. That's the distinction: labels
  for parallel items, not decoration on every line.)
- **An unearned claim.** "This single habit does more than everything else combined"
  was a confident, measurable-sounding assertion with nothing behind it. It's now
  the softer, honest "half the other tells fix themselves."
