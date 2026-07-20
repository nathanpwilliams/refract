-- =============================================================
-- Migration 0002 — interactive block types (Phase 0 of build plan)
-- Adds knowledge_check and scenario to content_blocks.block_type.
--
-- content shapes:
--   knowledge_check: inline, ungraded, instant feedback
--     {
--       "question": "...",
--       "options": [
--         {"id": "a", "text": "...", "correct": true,  "feedback": "..."},
--         {"id": "b", "text": "...", "correct": false, "feedback": "..."}
--       ],
--       "style": "single"            -- v1: single-select only
--     }
--
--   scenario: decision point with consequence feedback
--     {
--       "situation_md": "...",
--       "choices": [
--         {"id": "a", "text": "...", "consequence_md": "...",
--          "quality": "best" | "acceptable" | "poor"}
--       ]
--     }
--
-- ADAPTATION RULE (enforced in prompts + verifier): the pipeline may
-- rewrite question/situation/option WORDING for an audience, but must
-- never change which option is correct, option counts, or quality
-- ratings. The answer key is part of the assessment design, not prose.
-- =============================================================

alter table content_blocks
  drop constraint if exists content_blocks_block_type_check;

alter table content_blocks
  add constraint content_blocks_block_type_check
  check (block_type in
    ('heading','text','image','video_embed','callout','code','table',
     'quiz_ref','knowledge_check','scenario'));
