import React, { useState, useEffect, useCallback } from 'react'
import {
  UserRound, Save, MessageSquareText, Feather, Plus, Star, Trash2, ChevronDown, ChevronRight, ArrowLeft, Eye, EyeOff,
} from 'lucide-react'
import { useStore } from '../../store'
import { aboutYouApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500'
const cardClass = 'bg-gray-800/40 border border-gray-700/50 rounded-lg p-4 space-y-4'
const buttonClass = 'flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50 transition-colors'
const smallButtonClass = 'flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border border-gray-700 text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors'

const LENGTHS = [
  { id: 'short', label: 'Short', hint: 'A few sentences; offers to say more' },
  { id: 'default', label: 'Normal', hint: "The agent's usual length" },
  { id: 'detailed', label: 'Detailed', hint: 'Reasoning, context and examples' },
]
const FORMATS = [
  { id: 'auto', label: 'Automatic', hint: 'Markdown where the app shows it, plain text elsewhere' },
  { id: 'plain', label: 'Plain text', hint: 'Never use Markdown' },
  { id: 'markdown', label: 'Markdown', hint: 'Always use Markdown' },
]

const wordCount = (text) => (text.match(/\S+/g) || []).length

function SectionHeading({ icon: Icon, title, children }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 mb-1 flex items-center gap-1.5">
        <Icon className="w-4 h-4 text-brand-400" /> {title}
      </h3>
      <p className="text-xs text-gray-500">{children}</p>
    </div>
  )
}

// ── About you ───────────────────────────────────────────────────────────────

function AboutSection({ initial, limit, onSaved }) {
  const [text, setText] = useState(initial)
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)
  useEffect(() => setText(initial), [initial])

  const save = async () => {
    setSaving(true)
    try {
      const res = await aboutYouApi.update({ about_you: text })
      onSaved(res.data)
      addNotification({ type: 'success', message: 'About you saved.' })
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
    setSaving(false)
  }

  return (
    <section className="space-y-3">
      <SectionHeading icon={UserRound} title="About you">
        What the agent should know about you: who you are, what you work on, what you already know well,
        how you like to work. It goes into every conversation, so keep it to what matters.
      </SectionHeading>
      <div className={cardClass}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={7}
          maxLength={limit}
          placeholder={"e.g. I run a small IT consultancy and do most of my research on networking and AI hardware. " +
            "I'm comfortable with Linux and Python; skip the basics. I live in Ohio and use metric only for science."}
          className={`${inputClass} resize-y min-h-[8rem]`}
        />
        <div className="flex items-center justify-between">
          <span className={`text-[11px] ${text.length > limit * 0.9 ? 'text-amber-400' : 'text-gray-500'}`}>
            {text.length.toLocaleString()} / {limit.toLocaleString()} characters
          </span>
          <button onClick={save} disabled={saving || text.trim() === initial} className={buttonClass}>
            <Save className="w-4 h-4" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </section>
  )
}

// ── Reply style ─────────────────────────────────────────────────────────────

function OptionSelect({ value, options, onChange, inheritLabel, ariaLabel }) {
  return (
    <select
      aria-label={ariaLabel}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
      className={`${inputClass} py-1.5 text-xs`}
    >
      {inheritLabel && <option value="">{inheritLabel}</option>}
      {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  )
}

function ReplyStyleSection({ profile, channels, onSaved }) {
  const [length, setLength] = useState(profile.reply_length)
  const [format, setFormat] = useState(profile.formatting)
  const [perApp, setPerApp] = useState(
    Object.keys(profile.reply_length.channels).length + Object.keys(profile.formatting.channels).length > 0)
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)
  const savedKey = JSON.stringify([profile.reply_length, profile.formatting])
  // Reset to what is saved only when the saved values change (not when About you is saved).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setLength(profile.reply_length); setFormat(profile.formatting) }, [savedKey])

  const dirty = JSON.stringify(length) !== JSON.stringify(profile.reply_length) ||
    JSON.stringify(format) !== JSON.stringify(profile.formatting)

  const setChannel = (setter) => (channel, value) => setter((cur) => {
    const next = { ...cur.channels }
    if (value) next[channel] = value
    else delete next[channel]
    return { ...cur, channels: next }
  })

  const save = async () => {
    setSaving(true)
    try {
      const res = await aboutYouApi.update({ reply_length: length, formatting: format })
      onSaved(res.data)
      addNotification({ type: 'success', message: 'Reply style saved.' })
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
    setSaving(false)
  }

  const label = (opts, id) => opts.find((o) => o.id === id)?.label
  const formatResult = (channel, fmt) => {
    if (fmt === 'auto') return channel.renders_markdown ? 'Markdown' : 'plain text'
    return fmt === 'plain' ? 'plain text' : 'Markdown'
  }

  return (
    <section className="space-y-3">
      <SectionHeading icon={MessageSquareText} title="Reply style">
        How long the agent's chat replies are and whether they use Markdown. Documents and drafts you ask for keep
        their full length. Background tasks are not affected.
      </SectionHeading>
      <div className={cardClass}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-gray-300 mb-1">Reply length</label>
            <OptionSelect ariaLabel="Reply length" value={length.global} options={LENGTHS}
              onChange={(v) => setLength((c) => ({ ...c, global: v }))} />
            <span className="text-[10px] text-gray-500 block mt-1">{LENGTHS.find((o) => o.id === length.global)?.hint}</span>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-300 mb-1 flex items-center gap-1">
              Formatting
              <InfoTooltip text="Telegram, Slack and Matrix show the agent's replies as plain text, so Markdown symbols (#, **, tables) appear as-is there. Automatic asks for plain text in those apps and Markdown in the web chat, Discord and Mattermost." />
            </label>
            <OptionSelect ariaLabel="Formatting" value={format.global} options={FORMATS}
              onChange={(v) => setFormat((c) => ({ ...c, global: v }))} />
            <span className="text-[10px] text-gray-500 block mt-1">{FORMATS.find((o) => o.id === format.global)?.hint}</span>
          </div>
        </div>

        <div className="border-t border-gray-700/50 pt-3">
          <button
            type="button"
            onClick={() => setPerApp(!perApp)}
            className="flex items-center gap-1 text-xs font-semibold text-gray-300 hover:text-gray-100"
            aria-expanded={perApp}
          >
            {perApp ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            Different settings per app
          </button>
          {perApp && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-gray-500">
                    <th className="font-medium pb-2 pr-3">App</th>
                    <th className="font-medium pb-2 pr-3">Reply length</th>
                    <th className="font-medium pb-2 pr-3">Formatting</th>
                    <th className="font-medium pb-2">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((ch) => {
                    const l = length.channels[ch.id] || length.global
                    const f = format.channels[ch.id] || format.global
                    return (
                      <tr key={ch.id} className="border-t border-gray-800">
                        <td className="py-2 pr-3 text-gray-300 whitespace-nowrap">{ch.label}</td>
                        <td className="py-2 pr-3 min-w-[9rem]">
                          <OptionSelect ariaLabel={`${ch.label} reply length`} value={length.channels[ch.id]}
                            options={LENGTHS} inheritLabel={`Default (${label(LENGTHS, length.global)})`}
                            onChange={(v) => setChannel(setLength)(ch.id, v)} />
                        </td>
                        <td className="py-2 pr-3 min-w-[9rem]">
                          <OptionSelect ariaLabel={`${ch.label} formatting`} value={format.channels[ch.id]}
                            options={FORMATS} inheritLabel={`Default (${label(FORMATS, format.global)})`}
                            onChange={(v) => setChannel(setFormat)(ch.id, v)} />
                        </td>
                        <td className="py-2 text-gray-500 whitespace-nowrap">
                          {label(LENGTHS, l).toLowerCase()}, {formatResult(ch, f)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex justify-end">
          <button onClick={save} disabled={saving || !dirty} className={buttonClass}>
            <Save className="w-4 h-4" />
            {saving ? 'Saving...' : 'Save reply style'}
          </button>
        </div>
      </div>
    </section>
  )
}

// ── Writing voices ──────────────────────────────────────────────────────────

function sampleHealth(count) {
  if (count === 0) return { label: 'No samples yet', cls: 'text-gray-400 border-gray-600' }
  if (count < 3) return { label: `${count} sample${count === 1 ? '' : 's'} — add a few more`, cls: 'text-amber-300 border-amber-700/60' }
  if (count < 5) return { label: `${count} samples — good`, cls: 'text-emerald-300 border-emerald-700/60' }
  return { label: `${count} samples — strong`, cls: 'text-emerald-300 border-emerald-700/60' }
}

function HealthChip({ count }) {
  const h = sampleHealth(count)
  return <span className={`text-[10px] px-1.5 py-0.5 rounded border ${h.cls} whitespace-nowrap`}>{h.label}</span>
}

function NewVoiceForm({ limits, onCreated, onCancel, isFirst }) {
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const create = async () => {
    setSaving(true)
    try {
      const res = await aboutYouApi.createVoice({ name, notes, is_default: isFirst })
      onCreated(res.data)
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
      setSaving(false)
    }
  }

  return (
    <div className={cardClass}>
      <div>
        <label className="block text-xs font-semibold text-gray-300 mb-1">Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={limits.voice_name}
          placeholder="e.g. Blog, Work email, Newsletter" className={inputClass} autoFocus />
      </div>
      <div>
        <label className="block text-xs font-semibold text-gray-300 mb-1">Notes (optional)</label>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={limits.voice_notes}
          placeholder="Describe the style in your own words, and list words or phrases to avoid."
          className={`${inputClass} resize-y`} />
      </div>
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className={smallButtonClass}>Cancel</button>
        <button onClick={create} disabled={saving || !name.trim()} className={buttonClass}>
          <Plus className="w-4 h-4" /> {saving ? 'Creating...' : 'Create voice'}
        </button>
      </div>
    </div>
  )
}

function SampleRow({ voiceId, sample, onDelete }) {
  const [open, setOpen] = useState(false)
  const [content, setContent] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

  const toggle = async () => {
    if (!open && content === null) {
      try {
        const res = await aboutYouApi.getSample(voiceId, sample.id)
        setContent(res.data.content)
      } catch (e) {
        addNotification({ type: 'error', message: e.message })
        return
      }
    }
    setOpen(!open)
  }

  return (
    <li className="border border-gray-700/50 rounded-md p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-gray-200 truncate">{sample.title}</div>
          <div className="text-[10px] text-gray-500">
            {sample.word_count.toLocaleString()} words
            {sample.source_url && <> · <a href={sample.source_url} target="_blank" rel="noreferrer noopener"
              className="text-brand-400 hover:underline">source</a></>}
          </div>
          {!open && <p className="text-[11px] text-gray-400 mt-1 line-clamp-2">{sample.preview}</p>}
        </div>
        <div className="flex gap-1 flex-shrink-0">
          <button onClick={toggle} className={smallButtonClass} aria-label={open ? 'Hide sample' : 'Show sample'}>
            {open ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          </button>
          <button onClick={() => onDelete(sample)} className={smallButtonClass} aria-label="Delete sample">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
      {open && content !== null && (
        <pre className="mt-2 text-[11px] text-gray-300 whitespace-pre-wrap font-sans max-h-80 overflow-y-auto scrollbar-thin">
          {content}
        </pre>
      )}
    </li>
  )
}

function AddSampleForm({ voiceId, limits, onAdded }) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [url, setUrl] = useState('')
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)
  const words = wordCount(content)

  const add = async () => {
    setSaving(true)
    try {
      await aboutYouApi.addSample(voiceId, { title, content, source_url: url || null })
      setTitle(''); setContent(''); setUrl('')
      onAdded()
      addNotification({ type: 'success', message: 'Sample added.' })
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3 border-t border-gray-700/50 pt-4">
      <div className="text-xs font-semibold text-gray-300">Add a sample</div>
      <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={limits.sample_title}
        placeholder="Title (optional)" className={inputClass} />
      <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={8} maxLength={limits.sample_chars}
        placeholder="Paste something you wrote: a post, an email, a chapter. Your own words only."
        className={`${inputClass} resize-y min-h-[8rem]`} />
      <input value={url} onChange={(e) => setUrl(e.target.value)}
        placeholder="Link to where it was published (optional)" className={inputClass} />
      <div className="flex items-center justify-between">
        <span className={`text-[11px] ${words > 0 && words < 200 ? 'text-amber-400' : 'text-gray-500'}`}>
          {words.toLocaleString()} words{words > 0 && words < 200 ? ' — samples of 200+ words work best' : ''}
        </span>
        <button onClick={add} disabled={saving || !content.trim()} className={buttonClass}>
          <Plus className="w-4 h-4" /> {saving ? 'Adding...' : 'Add sample'}
        </button>
      </div>
    </div>
  )
}

function VoiceEditor({ voiceId, limits, onBack, onChanged }) {
  const [voice, setVoice] = useState(null)
  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const load = useCallback(async () => {
    try {
      const res = await aboutYouApi.getVoice(voiceId)
      setVoice(res.data)
      setName(res.data.name)
      setNotes(res.data.notes)
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
      onBack()
    }
  }, [voiceId, addNotification, onBack])

  useEffect(() => { load() }, [load])

  if (!voice) return <div className="text-xs text-gray-500">Loading voice...</div>

  const dirty = name.trim() !== voice.name || notes.trim() !== voice.notes
  const refresh = async () => { await load(); onChanged() }

  const save = async () => {
    setSaving(true)
    try {
      await aboutYouApi.updateVoice(voiceId, { name, notes })
      await refresh()
      addNotification({ type: 'success', message: 'Voice saved.' })
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
    setSaving(false)
  }

  const makeDefault = async () => {
    try {
      await aboutYouApi.updateVoice(voiceId, { is_default: true })
      await refresh()
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
  }

  const remove = async () => {
    if (!window.confirm(`Delete the voice "${voice.name}" and its ${voice.sample_count} samples?`)) return
    try {
      await aboutYouApi.deleteVoice(voiceId)
      onChanged()
      onBack()
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
  }

  const removeSample = async (sample) => {
    if (!window.confirm(`Delete the sample "${sample.title}"?`)) return
    try {
      await aboutYouApi.deleteSample(voiceId, sample.id)
      await refresh()
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
    }
  }

  return (
    <div className="space-y-3">
      <button onClick={onBack} className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200">
        <ArrowLeft className="w-3.5 h-3.5" /> All voices
      </button>
      <div className={cardClass}>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <HealthChip count={voice.sample_count} />
            {voice.is_default
              ? <span className="text-[10px] px-1.5 py-0.5 rounded border border-brand-600/60 text-brand-300">Default</span>
              : <button onClick={makeDefault} className={smallButtonClass}><Star className="w-3.5 h-3.5" /> Make default</button>}
          </div>
          <button onClick={remove} className={`${smallButtonClass} hover:text-red-300 hover:border-red-800`}>
            <Trash2 className="w-3.5 h-3.5" /> Delete voice
          </button>
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-300 mb-1">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={limits.voice_name} className={inputClass} />
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-300 mb-1">Notes</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} maxLength={limits.voice_notes}
            placeholder={'Describe the style, e.g. "Conversational, first person, short paragraphs, dry humour. ' +
              'Never use: delve, leverage, game-changer, em-dashes."'}
            className={`${inputClass} resize-y`} />
          <span className="text-[10px] text-gray-500 block mt-1">The agent treats anything you say to avoid here as a hard rule.</span>
        </div>
        <div className="flex justify-end">
          <button onClick={save} disabled={saving || !dirty || !name.trim()} className={buttonClass}>
            <Save className="w-4 h-4" /> {saving ? 'Saving...' : 'Save'}
          </button>
        </div>

        <div className="border-t border-gray-700/50 pt-4 space-y-2">
          <div className="text-xs font-semibold text-gray-300">
            Samples <span className="font-normal text-gray-500">({voice.total_words.toLocaleString()} words)</span>
          </div>
          {voice.samples.length === 0
            ? <p className="text-[11px] text-gray-500">No samples yet. Three to five pieces of 200+ words each, on different topics, work best.</p>
            : <ul className="space-y-2">{voice.samples.map((s) => (
                <SampleRow key={s.id} voiceId={voiceId} sample={s} onDelete={removeSample} />))}</ul>}
          <p className="text-[10px] text-gray-500">The agent reads excerpts of the three newest samples when it writes in this voice.</p>
        </div>

        {voice.sample_count < limits.samples_per_voice && (
          <AddSampleForm voiceId={voiceId} limits={limits} onAdded={refresh} />
        )}
      </div>
    </div>
  )
}

function WritingVoicesSection({ limits }) {
  const [voices, setVoices] = useState(null)
  const [creating, setCreating] = useState(false)
  const [openId, setOpenId] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

  const load = useCallback(async () => {
    try {
      const res = await aboutYouApi.listVoices()
      setVoices(res.data.voices)
    } catch (e) {
      addNotification({ type: 'error', message: e.message })
      setVoices([])
    }
  }, [addNotification])

  useEffect(() => { load() }, [load])
  const back = useCallback(() => setOpenId(null), [])

  return (
    <section className="space-y-3">
      <SectionHeading icon={Feather} title="Writing voices">
        Samples of your own writing, so drafts the agent writes for you sound like you. Ask for a draft
        "in my Blog voice", or just ask for a post or email and it uses your default voice.
      </SectionHeading>
      {voices === null ? (
        <div className="text-xs text-gray-500">Loading voices...</div>
      ) : openId ? (
        <VoiceEditor voiceId={openId} limits={limits} onBack={back} onChanged={load} />
      ) : (
        <>
          {voices.length > 0 && (
            <ul className="space-y-2">
              {voices.map((v) => (
                <li key={v.id}>
                  <button
                    onClick={() => setOpenId(v.id)}
                    className="w-full text-left bg-gray-800/40 border border-gray-700/50 hover:border-gray-600 rounded-lg px-4 py-3 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span className="text-sm text-gray-200 flex items-center gap-1.5">
                        {v.name}
                        {v.is_default && <Star className="w-3.5 h-3.5 text-brand-400" aria-label="Default voice" />}
                      </span>
                      <HealthChip count={v.sample_count} />
                    </div>
                    {v.notes && <p className="text-[11px] text-gray-500 mt-1 line-clamp-1">{v.notes}</p>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {creating ? (
            <NewVoiceForm
              limits={limits}
              isFirst={voices.length === 0}
              onCancel={() => setCreating(false)}
              onCreated={async (v) => { setCreating(false); await load(); setOpenId(v.id) }}
            />
          ) : voices.length < limits.voices && (
            <button onClick={() => setCreating(true)} className={smallButtonClass}>
              <Plus className="w-3.5 h-3.5" /> New voice
            </button>
          )}
        </>
      )}
    </section>
  )
}

// ── Tab ─────────────────────────────────────────────────────────────────────

export default function AboutYouSettings() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    aboutYouApi.get().then((res) => setData(res.data)).catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="text-xs text-red-400">Could not load About you: {error}</div>
  if (!data) return <div className="text-xs text-gray-500">Loading About you...</div>

  const onSaved = (profile) => setData((d) => ({ ...d, ...profile }))

  return (
    <div className="space-y-10">
      <AboutSection initial={data.about_you} limit={data.limits.about_you} onSaved={onSaved} />
      <ReplyStyleSection profile={data} channels={data.channels} onSaved={onSaved} />
      <WritingVoicesSection limits={data.limits} />
    </div>
  )
}
