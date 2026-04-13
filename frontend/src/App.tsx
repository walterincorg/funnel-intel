import {
  ArrowUp,
  Cable,
  Check,
  ChevronUp,
  Plus,
  Link2,
  Paperclip,
  Phone,
  Search,
  Settings,
  Sparkles,
  SquareTerminal,
  X,
} from 'lucide-react'
import { type ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import { api, type ChatMessage, type ChatModelPreset } from '@/api/client'

type Tab = 'Comms' | 'Operations' | 'Admin' | 'Growth' | 'Insights'

type UseCaseCard = {
  title: string
  description: string
  prompt: string
  featured?: boolean
}

type ConnectionsTab = 'Apps' | 'Custom API' | 'Custom MCP'

type ConnectionItem = {
  id: string
  name: string
  description: string
  logo?: string
}

type ModelOption = {
  id: ChatModelPreset
  label: string
  modelName: string
  priceTier: string
  description: string
}

type ChatThread = {
  id: string
  title: string
  messages: ChatMessage[]
}

function integrationClass(integration: string): string {
  return integration.toLowerCase().replace(/[^a-z0-9]+/g, '-')
}

const fallbackConnections: ConnectionItem[] = [
  {
    id: 'gmail',
    name: 'Gmail',
    description: 'An integration with Gmail for reading, searching, and sending emails.',
  },
  {
    id: 'google-calendar',
    name: 'Google Calendar',
    description: "Allows viewing and modifying a user's calendar schedule.",
  },
  {
    id: 'google-drive',
    name: 'Google Drive',
    description: 'Allows search and file access, plus docs and sheets editing.',
  },
  {
    id: 'slack',
    name: 'Slack',
    description: 'An integration with the Slack team messaging platform.',
  },
  {
    id: 'notion',
    name: 'Notion',
    description: 'Access and manage your Notion workspace pages and databases.',
  },
  {
    id: 'notion-database',
    name: 'Notion Database',
    description: 'Query Notion databases with filtering, sorting, and pagination.',
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    description: 'CRM integration for managing contacts and companies.',
  },
  {
    id: 'github',
    name: 'GitHub',
    description: 'Source code management integration for repositories and issues.',
  },
]

const tabs: Tab[] = ['Comms', 'Operations', 'Admin', 'Growth', 'Insights']

const modelOptions: ModelOption[] = [
  {
    id: 'basic',
    label: 'Basic',
    modelName: 'Haiku 4.5',
    priceTier: '$',
    description: 'Fast and cost-effective for frequent tasks',
  },
  {
    id: 'advanced',
    label: 'Advanced',
    modelName: 'Sonnet 4.6',
    priceTier: '$$',
    description: 'Balanced thinking for most tasks',
  },
  {
    id: 'expert',
    label: 'Expert',
    modelName: 'Opus 4.6',
    priceTier: '$$$',
    description: 'Smartest model for complex tasks',
  },
  {
    id: 'genius',
    label: 'Genius',
    modelName: 'Opus 4.6',
    priceTier: '$$$$',
    description: 'Smartest model with extended context and reasoning for the hardest tasks',
  },
]

const initialThreads: ChatThread[] = [
  {
    id: 'thread-1',
    title: 'Daily Work Briefing Agent',
    messages: [
      {
        role: 'assistant',
        content:
          "I'll set up a daily briefing workflow for you. I can connect your calendar and inbox, summarize priorities each morning, and send it to Slack or email.",
      },
      {
        role: 'assistant',
        content:
          'To start, do you want the briefing delivered at a fixed time each day or triggered after your first meeting?',
      },
    ],
  },
  {
    id: 'thread-2',
    title: 'LinkedIn Lead Pipeline Automation',
    messages: [
      {
        role: 'assistant',
        content:
          "Great - here's what I need to set up for this use case:\n1. Apify LinkedIn API for profile/search scraping\n2. Hunter.io for email enrichment\n3. HubSpot for pipeline sync",
      },
      {
        role: 'assistant',
        content:
          'Let me get the connections ready in the meantime - starting with the virtual computer for browser-based LinkedIn flows.',
      },
    ],
  },
  {
    id: 'thread-3',
    title: 'Subscription Audit and Cancellation',
    messages: [
      {
        role: 'assistant',
        content:
          "I can audit recurring charges from your receipts and bank history, flag low-usage subscriptions, then prepare cancellation actions for your approval.",
      },
    ],
  },
]

const useCasesByTab: Record<Tab, UseCaseCard[]> = {
  Comms: [
    {
      title: 'Outbound follow-ups',
      description:
        'Draft personalized email follow-ups after meetings and send when recipients are most likely to respond.',
      prompt:
        'Draft personalized follow-up emails for everyone I met this week and queue them for tomorrow morning.',
    },
    {
      title: 'Inbox triage',
      description:
        'Sort inbound emails by urgency and prepare suggested responses so important threads get handled first.',
      prompt: 'Triage my inbox by urgency and draft suggested responses for anything marked urgent.',
    },
    {
      title: 'LinkedIn messaging',
      description:
        'Create tailored LinkedIn outreach from account context and keep ongoing conversations on-brand.',
      prompt: 'Draft 5 tailored LinkedIn outreach messages for founders in fintech.',
    },
    {
      title: 'Meeting prep briefs',
      description:
        'Before each meeting, build a compact brief with recent updates, stakeholder notes, and talking points.',
      prompt: "Prepare briefing notes for tomorrow's meetings with talking points and recent updates.",
    },
    {
      title: 'Daily comms recap',
      description:
        'Compile a daily summary across messages, calls, and action items so nothing slips through.',
      prompt: 'Generate a daily recap of my messages, calls, and outstanding action items.',
    },
    {
      title: 'Call notes handoff',
      description:
        'Turn call transcripts into clean follow-up notes and push action items to your task tracker.',
      prompt: 'Turn today’s call transcripts into follow-up notes and create tasks for each action item.',
    },
  ],
  Operations: [
    {
      title: 'Chief of staff',
      description:
        "Be my chief of staff. Monitor my team's Slack channels and Jira, synthesize what's happening across the org, and tell me what needs my attention today.",
      prompt:
        "Act as my chief of staff: summarize org updates from Slack and Jira and flag what needs my attention today.",
    },
    {
      title: 'Stripe accounting',
      description:
        'When a payment comes through a Stripe webhook, log it in my accounting spreadsheet and update the customer record.',
      prompt:
        'When Stripe payments come in, log them in accounting and update corresponding customer records.',
    },
    {
      title: 'Schedule to Hootsuite',
      description:
        "When I move a content piece to 'Ready to Publish' in Notion, schedule in Hootsuite via API and notify the marketing channel.",
      prompt:
        "When a Notion page moves to 'Ready to Publish', schedule it in Hootsuite and notify marketing in Slack.",
    },
    {
      title: 'QuickBooks reconcile',
      description:
        'Every Monday, reconcile my QuickBooks entries against my bank transactions and flag mismatches.',
      prompt:
        'Every Monday, reconcile QuickBooks transactions against bank activity and flag mismatches.',
    },
    {
      title: 'Confluence changelog',
      description:
        'When changes merge to main in Github, update our Confluence changelog with what shipped.',
      prompt: 'Update our Confluence changelog automatically whenever commits merge into main.',
    },
    {
      title: 'Business backend',
      description:
        'Be the backend for my business. Process incoming webhooks, route requests to the right people, and update my Supabase automatically.',
      prompt:
        'Process incoming webhooks, route requests to the right owners, and sync everything to Supabase.',
    },
  ],
  Admin: [
    {
      title: 'Collect receipts',
      description:
        'Download receipts from my Gmail and Doordash using the computer, then upload everything to my expense tracking system.',
      prompt:
        'Collect receipts from Gmail and Doordash, then upload them into my expense tracking system.',
    },
    {
      title: 'Paralegal',
      description:
        'Be my paralegal. Review contracts for unusual terms, track deadlines with the Clio API, prepare document summaries, and flag anything that needs attorney review.',
      prompt:
        'Review my latest contracts for unusual terms, summarize them, and flag anything requiring attorney review.',
    },
    {
      title: 'Personal assistant',
      description:
        'Be my personal assistant. Connect to my email, calendar, and tasks, and set up texting so I can message you to do things throughout the day.',
      prompt:
        'Be my personal assistant: connect email, calendar, and tasks so I can text you requests all day.',
    },
    {
      title: 'Cancel subscriptions',
      description:
        'Review all my subscriptions across email receipts and bank statements, identify unused ones, and use the computer to cancel them.',
      prompt:
        'Audit all subscriptions across receipts and bank statements, identify unused ones, and cancel them.',
    },
    {
      title: 'HR docs',
      description:
        "Log into my company's HR portal, download my pay stubs and tax documents, and file them in Dropbox.",
      prompt: 'Download my HR tax/pay documents and file everything to Dropbox in the right folders.',
    },
    {
      title: 'Office space search',
      description:
        'Use the computer to find me office space, reach out to landlords, and handle the negotiation until we have a signed lease.',
      prompt:
        'Find office spaces, contact landlords, and handle negotiations until we get a signed lease.',
    },
  ],
  Growth: [
    {
      title: 'Email tracking',
      description:
        'Embed a tracking pixel via webhook in my outbound emails. When a prospect opens my email, research them and draft a personalized follow-up.',
      prompt:
        'Add a tracking pixel to outbound emails, and when prospects open, research them and draft follow-ups.',
    },
    {
      title: 'Daily LinkedIn',
      description:
        'Every day, connect with people in my industry and repost relevant posts on LinkedIn.',
      prompt: 'Every day, connect with people in my industry and repost relevant LinkedIn posts.',
      featured: true,
    },
    {
      title: 'Calendly briefing',
      description:
        "When I get a new Calendly booking via webhook, research the attendee's company and post a briefing to Slack.",
      prompt:
        "When I get a Calendly booking, research the attendee's company and post a concise briefing to Slack.",
    },
    {
      title: 'LinkedIn lead sourcing',
      description:
        'Use the virtual computer to scrape LinkedIn, find emails with Hunter.io API, and add leads to my HubSpot pipeline.',
      prompt:
        'Use the virtual computer to source LinkedIn leads, enrich with Hunter, and add them to HubSpot.',
    },
    {
      title: 'Sales qualification',
      description:
        "Autonomously handle initial sales conversations until leads are qualified, then text me when it's ready for hand off.",
      prompt:
        'Handle initial sales conversations autonomously until qualified, then text me for handoff.',
    },
    {
      title: 'Customer outings',
      description:
        "Find high-value customers we haven't contacted in a while and find some outing events I can invite them to based on their interests logged in our CRM.",
      prompt:
        "Find high-value customers we haven't contacted and suggest events to invite them to based on CRM interests.",
    },
  ],
  Insights: [
    {
      title: 'Competitor monitoring',
      description:
        'Monitor competitor websites for pricing or feature changes - for major announcements, research and tell me what it means for our positioning.',
      prompt:
        'Monitor competitor sites for pricing/feature changes and summarize strategic implications weekly.',
    },
    {
      title: 'Research deep dives',
      description:
        'When I label an email "research", do a deep dive on the topic using Perplexity and reply with findings.',
      prompt: 'When I label an email "research", run a deep dive and reply with key findings.',
    },
    {
      title: 'Weekly exec summary',
      description:
        'Create a weekly executive summary of work completed in Linear and GitHub - call out trends and areas that need my attention.',
      prompt:
        'Create a weekly executive summary from Linear and GitHub with trends and attention areas.',
    },
    {
      title: 'Monitor podcasts',
      description:
        'Monitor these podcasts and blogs for discussions relevant to my industry and email me a summary when something interesting drops.',
      prompt:
        'Monitor selected podcasts/blogs for industry mentions and email me summaries when relevant topics appear.',
    },
    {
      title: 'Sales call analysis',
      description:
        "Review my team's sales calls weekly. Identify what top performers do differently and send coaching notes to under performers with specific, actionable feedback.",
      prompt:
        "Review this week's sales calls, identify top-performer patterns, and send coaching notes to underperformers.",
    },
    {
      title: 'Company research',
      description:
        'When I add a company to my Airtable tracker, research their tech stack, funding, and key contacts.',
      prompt:
        'When a company is added to Airtable, research its stack, funding, and key contacts automatically.',
    },
  ],
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('Growth')
  const [composerText, setComposerText] = useState('')
  const [isConnectionsMenuOpen, setIsConnectionsMenuOpen] = useState(false)
  const [isConnectionsModalOpen, setIsConnectionsModalOpen] = useState(false)
  const [connectionsTab, setConnectionsTab] = useState<ConnectionsTab>('Apps')
  const [connectionsSearch, setConnectionsSearch] = useState('')
  const [connectionsLoading, setConnectionsLoading] = useState(false)
  const [connectionsError, setConnectionsError] = useState<string | null>(null)
  const [connections, setConnections] = useState<ConnectionItem[]>(fallbackConnections)
  const [chatThreads, setChatThreads] = useState<ChatThread[]>(initialThreads)
  const [activeChatId, setActiveChatId] = useState(initialThreads[0].id)
  const [isHomeView, setIsHomeView] = useState(true)
  const [chatSearch, setChatSearch] = useState('')
  const [chatError, setChatError] = useState<string | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [isModelMenuOpen, setIsModelMenuOpen] = useState(false)
  const [selectedModelPreset, setSelectedModelPreset] = useState<ChatModelPreset>('advanced')
  const requestAbortRef = useRef<AbortController | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const activeCards = useCasesByTab[activeTab]
  const canSend = composerText.trim().length > 0
  const composioApiKey = (import.meta.env.VITE_COMPOSIO_API_KEY as string | undefined)?.trim()

  const shownConnections = useMemo(() => {
    if (!connectionsSearch.trim()) {
      return connections
    }

    const query = connectionsSearch.toLowerCase()
    return connections.filter(
      (item) =>
        item.name.toLowerCase().includes(query) || item.description.toLowerCase().includes(query),
    )
  }, [connections, connectionsSearch])
  const selectedModel = modelOptions.find((option) => option.id === selectedModelPreset) ?? modelOptions[1]
  const activeThread = useMemo(
    () => chatThreads.find((thread) => thread.id === activeChatId) ?? chatThreads[0] ?? null,
    [activeChatId, chatThreads],
  )
  const chatMessages = activeThread?.messages ?? []
  const visibleThreads = useMemo(() => {
    const query = chatSearch.trim().toLowerCase()
    if (!query) {
      return chatThreads
    }

    return chatThreads.filter((thread) => thread.title.toLowerCase().includes(query))
  }, [chatSearch, chatThreads])

  function onUseCaseClick(card: UseCaseCard): void {
    setComposerText(card.prompt)
  }

  function createThreadTitle(fromMessage: string): string {
    const compact = fromMessage.replace(/\s+/g, ' ').trim()
    if (!compact) {
      return `New Agent ${chatThreads.length + 1}`
    }
    if (compact.length <= 38) {
      return compact
    }
    return `${compact.slice(0, 38).trim()}...`
  }

  async function onSend(): Promise<void> {
    if (!canSend || isSending) {
      return
    }

    const message = composerText.trim()
    const shouldCreateFromHome = isHomeView || !activeThread
    let currentThreadId = activeThread?.id ?? ''
    let history: ChatMessage[] = []

    if (shouldCreateFromHome) {
      const newThread: ChatThread = {
        id: `thread-${Date.now()}`,
        title: createThreadTitle(message),
        messages: [{ role: 'user', content: message }],
      }
      currentThreadId = newThread.id
      setChatThreads((prev) => [newThread, ...prev])
      setActiveChatId(newThread.id)
      setIsHomeView(false)
    } else if (activeThread) {
      history = activeThread.messages.slice(-8)
      currentThreadId = activeThread.id
      setChatThreads((prev) =>
        prev.map((thread) =>
          thread.id === currentThreadId
            ? { ...thread, messages: [...thread.messages, { role: 'user', content: message }] }
            : thread,
        ),
      )
    }

    setChatError(null)
    setIsSending(true)
    const abortController = new AbortController()
    requestAbortRef.current = abortController
    setComposerText('')

    try {
      const response = await api.chat(message, history, selectedModelPreset, abortController.signal)
      setChatThreads((prev) =>
        prev.map((thread) =>
          thread.id === currentThreadId
            ? { ...thread, messages: [...thread.messages, { role: 'assistant', content: response.reply }] }
            : thread,
        ),
      )
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        console.error(error)
        setChatError('The assistant could not respond right now. Check OpenRouter API key/server logs.')
      }
    } finally {
      requestAbortRef.current = null
      setIsSending(false)
    }
  }

  function onStopGeneration(): void {
    requestAbortRef.current?.abort()
  }

  function onCreateNewAgent(): void {
    const nextIndex = chatThreads.length + 1
    const newThread: ChatThread = {
      id: `thread-${Date.now()}`,
      title: `New Agent ${nextIndex}`,
      messages: [],
    }
    setChatThreads((prev) => [newThread, ...prev])
    setActiveChatId(newThread.id)
    setIsHomeView(false)
    setComposerText('')
    setChatError(null)
  }

  function onAttachClick(): void {
    fileInputRef.current?.click()
  }

  function onAttachSelected(event: ChangeEvent<HTMLInputElement>): void {
    event.target.value = ''
  }

  function openConnectionsModal(): void {
    setIsConnectionsModalOpen(true)
    setIsConnectionsMenuOpen(false)
  }

  useEffect(() => {
    if (!isConnectionsModalOpen || connectionsTab !== 'Apps') {
      return
    }

    const apiKey = composioApiKey
    if (!apiKey) {
      setConnectionsError('Set VITE_COMPOSIO_API_KEY in frontend/.env.local to load live apps.')
      setConnections(fallbackConnections)
      return
    }

    const controller = new AbortController()
    const query = connectionsSearch.trim()
    const params = new URLSearchParams({
      limit: '24',
      sort_by: 'alphabetically',
      include_deprecated: 'false',
    })
    if (query) {
      params.set('search', query)
    }

    async function fetchConnections(): Promise<void> {
      setConnectionsLoading(true)
      setConnectionsError(null)
      try {
        const response = await fetch(`https://backend.composio.dev/api/v3.1/toolkits?${params}`, {
          headers: {
            'x-api-key': apiKey!,
          },
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error(`Composio responded with ${response.status}`)
        }

        const payload = (await response.json()) as {
          items?: Array<{
            slug: string
            name: string
            logo?: string
            app_url?: string
            description?: string
          }>
        }

        const items = payload.items ?? []
        if (items.length === 0) {
          setConnections(fallbackConnections)
          return
        }

        setConnections(
          items.map((item) => ({
            id: item.slug,
            name: item.name,
            logo: item.logo,
            description: item.description ?? item.app_url ?? `Connect ${item.name} with Walter.`,
          })),
        )
      } catch (error) {
        if (controller.signal.aborted) {
          return
        }

        setConnectionsError('Could not load live Composio apps. Showing fallback list.')
        setConnections(fallbackConnections)
        console.error(error)
      } finally {
        if (!controller.signal.aborted) {
          setConnectionsLoading(false)
        }
      }
    }

    void fetchConnections()

    return () => {
      controller.abort()
    }
  }, [composioApiKey, connectionsSearch, connectionsTab, isConnectionsModalOpen])

  return (
    <main className="app-shell">
      <input
        ref={fileInputRef}
        className="hidden-file-input"
        type="file"
        accept="image/*"
        multiple
        onChange={onAttachSelected}
      />
      <aside className="chat-sidebar">
        <div className="sidebar-top">
          <button type="button" className="sidebar-new" onClick={onCreateNewAgent}>
            New agent
          </button>
          <input
            ref={searchInputRef}
            className="sidebar-search-input"
            value={chatSearch}
            onChange={(event) => setChatSearch(event.target.value)}
            placeholder="Filter chats..."
          />
        </div>
        <div className="sidebar-section">Recent</div>
        <nav className="sidebar-chats">
          {visibleThreads.map((chat) => (
            <button
              key={chat.id}
              type="button"
              className={chat.id === activeChatId ? 'active' : ''}
              onClick={() => {
                setActiveChatId(chat.id)
                setIsHomeView(false)
                setChatError(null)
              }}
            >
              {chat.title}
            </button>
          ))}
          {visibleThreads.length === 0 && <div className="sidebar-empty">No chats found.</div>}
        </nav>
        <div className="sidebar-profile">
          <div className="avatar">N</div>
          <div>
            <strong>Nikolas Keller</strong>
            <span>Pro trial</span>
          </div>
        </div>
      </aside>

      <div className={`workspace ${isHomeView ? '' : 'chat-mode'}`.trim()}>
        <div className="page">
      {isHomeView ? (
        <>
      <section className="hero-shell">
        <header className="topbar">
          <div className="brand">
            <img src="/walter-assets/walter-icon.png" alt="Walter icon" />
            <span>Walter</span>
          </div>
        </header>

        <div className="hero-copy">
          <h1>What can I do for you?</h1>
          <span>You are trialing the Pro plan (3 days left) · Upgrade</span>
        </div>

        <div className="composer">
          <div className="composer-title">
            <input
              type="text"
              value={composerText}
              placeholder="Describe a task or responsibility"
              onChange={(event) => setComposerText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  void onSend()
                }
              }}
            />
            <button
              type="button"
              className="send-btn"
              onClick={() => void onSend()}
              disabled={!canSend || isSending}
            >
              <ArrowUp size={15} />
            </button>
          </div>
          <div className="composer-actions">
            <button type="button" aria-label="attach file" onClick={onAttachClick}>
              <Paperclip size={16} />
            </button>
            <div className="connections-anchor">
              <button
                type="button"
                aria-label="connections"
                className={isConnectionsMenuOpen ? 'active-tool' : ''}
                onClick={() => setIsConnectionsMenuOpen((value) => !value)}
              >
                <Cable size={16} />
              </button>
              {isConnectionsMenuOpen && (
                <div className="connections-menu">
                  <div className="menu-title">Your Connections</div>
                  <div className="menu-item account">
                    <div className="app-logo gmail">M</div>
                    <div>
                      <strong>Gmail</strong>
                      <span>niko@allenfjord.vc</span>
                    </div>
                    <button type="button" className="ghost-inline" onClick={openConnectionsModal}>
                      Activate
                    </button>
                  </div>
                  <button type="button" className="menu-item action" onClick={openConnectionsModal}>
                    <Plus size={16} />
                    New Connection
                  </button>
                  <button type="button" className="menu-item action" onClick={openConnectionsModal}>
                    <Settings size={16} />
                    Manage Connections
                  </button>
                </div>
              )}
            </div>
            <button type="button" aria-label="computer">
              <SquareTerminal size={16} />
            </button>
            <button type="button" aria-label="automations">
              <Sparkles size={16} />
            </button>
            <button type="button" aria-label="phone">
              <Phone size={16} />
            </button>
            <div className="composer-spacer" />
            <div className="model-picker">
              <button
                type="button"
                className="advanced"
                onClick={() => setIsModelMenuOpen((value) => !value)}
              >
                {selectedModel.label} {selectedModel.priceTier} <ChevronUp size={14} />
              </button>
              {isModelMenuOpen && (
                <div className="model-menu">
                  {modelOptions.map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      className="model-menu-item"
                      onClick={() => {
                        setSelectedModelPreset(option.id)
                        setIsModelMenuOpen(false)
                      }}
                    >
                      <div className="model-menu-head">
                        <strong>{option.label}</strong>
                        <span>{option.modelName}</span>
                        <em>{option.priceTier}</em>
                      </div>
                      <small>{option.description}</small>
                      {selectedModelPreset === option.id && <Check size={14} />}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" className="stop-btn" onClick={onStopGeneration} disabled={!isSending}>
              Stop
            </button>
          </div>
          <div className="composer-footer">
            <Link2 size={14} />
            Connect to any App, API, or MCP
          </div>
        </div>
      </section>
      <section className="use-cases">
        <div className="tabs">
          {tabs.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={tab === activeTab ? 'active' : ''}
            >
              {tab}
            </button>
          ))}
        </div>
        <div className="cards">
          {activeCards.map((card) => (
            <article
              key={card.title}
              className={card.featured ? 'featured' : ''}
              onClick={() => onUseCaseClick(card)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  onUseCaseClick(card)
                }
              }}
              role="button"
              tabIndex={0}
            >
              <div className="card-head">
                <h3>{card.title}</h3>
              </div>
              <p>{card.description}</p>
              <button type="button" className="try-btn">
                <Sparkles size={13} />
                Try it
              </button>
            </article>
          ))}
        </div>
      </section>
        </>
      ) : (
        <section className="chat-view">
          <header className="chat-view-head">{activeThread?.title ?? 'New Agent'}</header>
          <section className="chat-thread">
          {chatMessages.map((message, index) => (
            <article key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
              <div>{message.content}</div>
              {message.role === 'assistant' &&
                message.content.toLowerCase().includes('virtual computer') && (
                  <div className="virtual-computer-card">
                    <div className="vc-head">
                      <h4>Create a virtual computer?</h4>
                      <span>LinkedIn Scraping Computer</span>
                    </div>
                    <p>
                      A dedicated virtual computer lets the agent log into websites, run native apps,
                      and keep long-running desktop workflows active for you.
                    </p>
                    <div className="vc-ready">
                      <strong>Ready to create</strong>
                      <span>Click the button below to provision a new virtual computer.</span>
                    </div>
                    <button type="button">Create virtual computer</button>
                  </div>
                )}
            </article>
          ))}
          {chatMessages.length === 0 && <div className="chat-empty">Start the conversation below.</div>}
          {chatError && <div className="chat-error">{chatError}</div>}
          </section>
          <div className="chat-composer-wrap">
            <div className="composer">
              <div className="composer-title">
                <input
                  type="text"
                  value={composerText}
                  placeholder="Reply to Walter..."
                  onChange={(event) => setComposerText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      void onSend()
                    }
                  }}
                />
                <button
                  type="button"
                  className="send-btn"
                  onClick={() => void onSend()}
                  disabled={!canSend || isSending}
                >
                  <ArrowUp size={15} />
                </button>
              </div>
              <div className="composer-actions">
                <button type="button" aria-label="attach file" onClick={onAttachClick}>
                  <Paperclip size={16} />
                </button>
                <div className="connections-anchor">
                  <button
                    type="button"
                    aria-label="connections"
                    className={isConnectionsMenuOpen ? 'active-tool' : ''}
                    onClick={() => setIsConnectionsMenuOpen((value) => !value)}
                  >
                    <Cable size={16} />
                  </button>
                  {isConnectionsMenuOpen && (
                    <div className="connections-menu">
                      <div className="menu-title">Your Connections</div>
                      <div className="menu-item account">
                        <div className="app-logo gmail">M</div>
                        <div>
                          <strong>Gmail</strong>
                          <span>niko@allenfjord.vc</span>
                        </div>
                        <button type="button" className="ghost-inline" onClick={openConnectionsModal}>
                          Activate
                        </button>
                      </div>
                      <button type="button" className="menu-item action" onClick={openConnectionsModal}>
                        <Plus size={16} />
                        New Connection
                      </button>
                      <button type="button" className="menu-item action" onClick={openConnectionsModal}>
                        <Settings size={16} />
                        Manage Connections
                      </button>
                    </div>
                  )}
                </div>
                <button type="button" aria-label="computer">
                  <SquareTerminal size={16} />
                </button>
                <button type="button" aria-label="automations">
                  <Sparkles size={16} />
                </button>
                <button type="button" aria-label="phone">
                  <Phone size={16} />
                </button>
                <div className="composer-spacer" />
                <div className="model-picker">
                  <button
                    type="button"
                    className="advanced"
                    onClick={() => setIsModelMenuOpen((value) => !value)}
                  >
                    {selectedModel.label} {selectedModel.priceTier} <ChevronUp size={14} />
                  </button>
                  {isModelMenuOpen && (
                    <div className="model-menu">
                      {modelOptions.map((option) => (
                        <button
                          key={option.id}
                          type="button"
                          className="model-menu-item"
                          onClick={() => {
                            setSelectedModelPreset(option.id)
                            setIsModelMenuOpen(false)
                          }}
                        >
                          <div className="model-menu-head">
                            <strong>{option.label}</strong>
                            <span>{option.modelName}</span>
                            <em>{option.priceTier}</em>
                          </div>
                          <small>{option.description}</small>
                          {selectedModelPreset === option.id && <Check size={14} />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <button type="button" className="stop-btn" onClick={onStopGeneration} disabled={!isSending}>
                  Stop
                </button>
              </div>
              <div className="composer-footer">
                <Link2 size={14} />
                Connect to any App, API, or MCP
              </div>
            </div>
          </div>
        </section>
      )}

      {isConnectionsModalOpen && (
        <div className="connections-modal-backdrop" onClick={() => setIsConnectionsModalOpen(false)}>
          <div className="connections-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h2>Connections</h2>
                <p>Connect your Apps, APIs, and MCP servers to your agents.</p>
              </div>
              <button
                type="button"
                className="close-modal"
                onClick={() => setIsConnectionsModalOpen(false)}
              >
                <X size={18} />
              </button>
            </div>

            <div className="modal-tabs-row">
              {(['Apps', 'Custom API', 'Custom MCP'] as ConnectionsTab[]).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setConnectionsTab(tab)}
                  className={tab === connectionsTab ? 'active' : ''}
                >
                  {tab}
                </button>
              ))}

              <label className="modal-search">
                <Search size={16} />
                <input
                  value={connectionsSearch}
                  onChange={(event) => setConnectionsSearch(event.target.value)}
                  placeholder="Search"
                />
              </label>
            </div>

            {connectionsTab !== 'Apps' ? (
              <div className="modal-empty">
                {connectionsTab} setup is coming next. Use Apps for the Composio-powered catalog.
              </div>
            ) : (
              <>
                {connectionsError && <div className="modal-error">{connectionsError}</div>}
                {connectionsLoading && <div className="modal-loading">Loading Composio apps...</div>}
                <div className="connections-grid">
                  {shownConnections.map((item) => (
                    <article key={item.id} className="connection-card">
                      <div className={`app-logo ${integrationClass(item.name)}`}>
                        {item.logo ? (
                          <img src={item.logo} alt={`${item.name} logo`} />
                        ) : (
                          item.name.charAt(0).toUpperCase()
                        )}
                      </div>
                      <div>
                        <h3>{item.name}</h3>
                        <p>{item.description}</p>
                      </div>
                    </article>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}
        </div>
      </div>
    </main>
  )
}
