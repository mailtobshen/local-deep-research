#!/usr/bin/env python3
"""
Extract user-facing English strings from templates and JS files.
Very conservative - only extracts obviously user-facing text.

Usage:
    python scripts/extract_i18n_strings.py
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "src" / "local_deep_research" / "web" / "templates"
JS_DIR = PROJECT_ROOT / "src" / "local_deep_research" / "web" / "static" / "js"
OUTPUT_FILE = PROJECT_ROOT / "scripts" / "extracted_strings.json"

# Skip these templates that are already handled or special
SKIP_TEMPLATES = {"base.html"}

# Skip these JS files
SKIP_JS = {"i18n.js", "language_switcher.js", "theme.js", "app.js"}

# Words that indicate user-facing text (not code)
USER_WORDS = {
    'please', 'thank', 'welcome', 'sorry', 'error', 'success', 'warning',
    'loading', 'saving', 'deleting', 'creating', 'updating', 'searching',
    'submit', 'cancel', 'close', 'open', 'back', 'next', 'previous',
    'yes', 'no', 'ok', 'done', 'failed', 'complete', 'pending',
    'username', 'password', 'email', 'login', 'logout', 'register',
    'account', 'profile', 'settings', 'help', 'about', 'contact',
    'research', 'search', 'result', 'report', 'analysis', 'summary',
    'document', 'collection', 'library', 'download', 'upload',
    'title', 'name', 'description', 'date', 'time', 'status',
    'confirm', 'are you sure', 'cannot be undone', 'delete',
    'edit', 'view', 'add', 'remove', 'save', 'create', 'update',
    'your', 'you', 'this', 'that', 'these', 'the', 'a', 'an',
    'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has',
    'will', 'would', 'could', 'should', 'may', 'might', 'can',
    'must', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
    'from', 'as', 'into', 'through', 'during', 'before', 'after',
    'above', 'below', 'between', 'under', 'over', 'again', 'further',
    'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how',
    'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other',
    'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same',
    'so', 'than', 'too', 'very', 'just', 'now', 'also', 'get',
    'go', 'make', 'know', 'take', 'see', 'come', 'think', 'look',
    'want', 'give', 'use', 'find', 'tell', 'ask', 'work', 'seem',
    'feel', 'try', 'leave', 'call', 'keep', 'let', 'begin',
    'show', 'hear', 'play', 'run', 'move', 'live', 'believe',
    'hold', 'bring', 'happen', 'stand', 'lose', 'pay', 'meet',
    'include', 'continue', 'set', 'learn', 'change', 'lead',
    'understand', 'watch', 'follow', 'stop', 'create', 'speak',
    'read', 'allow', 'add', 'spend', 'grow', 'open', 'walk',
    'offer', 'remember', 'love', 'consider', 'appear', 'buy',
    'wait', 'serve', 'die', 'send', 'expect', 'build', 'stay',
    'fall', 'cut', 'reach', 'kill', 'remain', 'suggest',
    'raise', 'pass', 'sell', 'require', 'report', 'decide',
    'pull', 'return', 'explain', 'carry', 'develop', 'hope',
    'drive', 'break', 'receive', 'agree', 'support', 'remove',
    'return', 'describe', 'lie', 'discover', 'contain',
    'establish', 'join', 'reduce', 'settle', 'choose',
    'arise', 'relating', 'preparing', 'private', 'public',
    'notification', 'browser', 'permission', 'data', 'information',
    'content', 'file', 'folder', 'page', 'interface', 'server',
    'database', 'storage', 'memory', 'cache', 'network',
    'connection', 'request', 'response', 'invalid', 'required',
    'missing', 'unavailable', 'unknown', 'unexpected',
    'unable', 'cannot', 'could not', 'not found', 'access',
    'denied', 'forbidden', 'unauthorized', 'timeout',
    'expired', 'locked', 'disabled', 'enabled', 'active',
    'inactive', 'visible', 'hidden', 'public', 'private',
    'shared', 'encrypted', 'decrypted', 'protected', 'secure',
    'insecure', 'valid', 'invalid', 'correct', 'incorrect',
    'true', 'false', 'on', 'off', 'enabled', 'disabled',
    'started', 'stopped', 'paused', 'resumed', 'completed',
    'finished', 'cancelled', 'aborted', 'failed', 'succeeded',
    'queued', 'processing', 'running', 'waiting', 'ready',
    'available', 'unavailable', 'offline', 'online',
    'connected', 'disconnected', 'synced', 'unsynced',
    'saved', 'unsaved', 'modified', 'unmodified', 'new',
    'old', 'current', 'latest', 'previous', 'next', 'first',
    'last', 'total', 'count', 'number', 'amount', 'quantity',
    'size', 'length', 'width', 'height', 'depth', 'volume',
    'weight', 'speed', 'rate', 'ratio', 'percentage',
    'average', 'minimum', 'maximum', 'median', 'mean',
    'mode', 'range', 'deviation', 'variance', 'standard',
    'normal', 'abnormal', 'regular', 'irregular',
    'frequent', 'infrequent', 'common', 'uncommon',
    'rare', 'usual', 'unusual', 'typical', 'atypical',
    'default', 'custom', 'preset', 'configured',
    'recommended', 'suggested', 'optional', 'mandatory',
    'required', 'necessary', 'unnecessary', 'important',
    'unimportant', 'critical', 'non-critical', 'essential',
    'non-essential', 'primary', 'secondary', 'tertiary',
    'main', 'sub', 'minor', 'major', 'key', 'primary',
    'backup', 'archive', 'copy', 'duplicate', 'original',
    'source', 'target', 'destination', 'origin', 'path',
    'location', 'address', 'url', 'link', 'reference',
    'note', 'comment', 'annotation', 'remark', 'footnote',
    'citation', 'quote', 'reference', 'bibliography',
    'index', 'table', 'list', 'grid', 'chart', 'graph',
    'diagram', 'figure', 'image', 'picture', 'photo',
    'video', 'audio', 'sound', 'music', 'voice', 'text',
    'word', 'sentence', 'paragraph', 'chapter', 'section',
    'part', 'volume', 'issue', 'edition', 'version',
    'revision', 'update', 'upgrade', 'downgrade',
    'install', 'uninstall', 'setup', 'configure',
    'customize', 'personalize', 'optimize', 'improve',
    'enhance', 'upgrade', 'downgrade', 'backup', 'restore',
    'import', 'export', 'convert', 'transform', 'translate',
    'generate', 'create', 'build', 'compile', 'assemble',
    'deploy', 'publish', 'release', 'distribute',
    'share', 'send', 'receive', 'transfer', 'move',
    'copy', 'paste', 'cut', 'delete', 'remove', 'clear',
    'reset', 'undo', 'redo', 'refresh', 'reload', 'restart',
    'reboot', 'shutdown', 'power', 'sleep', 'wake',
    'lock', 'unlock', 'protect', 'unprotect', 'hide',
    'show', 'display', 'render', 'draw', 'paint', 'print',
    'scan', 'search', 'find', 'locate', 'identify',
    'recognize', 'detect', 'discover', 'reveal', 'expose',
    'cover', 'mask', 'filter', 'sort', 'order', 'arrange',
    'organize', 'group', 'category', 'tag', 'label',
    'mark', 'flag', 'highlight', 'emphasize', 'underline',
    'bold', 'italic', 'strikethrough', 'color', 'size',
    'font', 'style', 'format', 'layout', 'design',
    'theme', 'skin', 'appearance', 'look', 'feel',
    'behavior', 'action', 'reaction', 'response',
    'feedback', 'input', 'output', 'entry', 'exit',
    'start', 'end', 'begin', 'finish', 'stop', 'pause',
    'resume', 'continue', 'repeat', 'loop', 'iterate',
    'step', 'stage', 'phase', 'period', 'duration',
    'interval', 'frequency', 'schedule', 'plan',
    'strategy', 'method', 'approach', 'technique',
    'process', 'procedure', 'workflow', 'pipeline',
    'stream', 'flow', 'queue', 'stack', 'buffer',
    'channel', 'port', 'slot', 'socket', 'endpoint',
    'api', 'sdk', 'library', 'framework', 'platform',
    'system', 'environment', 'context', 'scope',
    'namespace', 'package', 'module', 'component',
    'element', 'object', 'entity', 'item', 'unit',
    'instance', 'example', 'sample', 'case', 'scenario',
    'situation', 'condition', 'state', 'status',
    'mode', 'type', 'kind', 'sort', 'form', 'shape',
    'structure', 'pattern', 'model', 'template',
    'prototype', 'instance', 'copy', 'clone',
    'original', 'version', 'variant', 'alternative',
    'option', 'choice', 'selection', 'preference',
    'setting', 'parameter', 'argument', 'variable',
    'constant', 'value', 'key', 'property', 'attribute',
    'field', 'column', 'row', 'cell', 'record',
    'dataset', 'database', 'table', 'schema',
    'index', 'key', 'constraint', 'rule', 'policy',
    'permission', 'access', 'right', 'privilege',
    'role', 'group', 'user', 'admin', 'owner',
    'member', 'guest', 'visitor', 'viewer',
    'editor', 'author', 'contributor', 'moderator',
    'subscriber', 'follower', 'friend', 'contact',
    'profile', 'account', 'identity', 'credential',
    'token', 'key', 'secret', 'password', 'pin',
    'code', 'hash', 'checksum', 'signature',
    'certificate', 'license', 'agreement', 'terms',
    'policy', 'privacy', 'security', 'safety',
    'protection', 'defense', 'guard', 'shield',
    'firewall', 'antivirus', 'malware', 'spam',
    'phishing', 'fraud', 'scam', 'hack', 'breach',
    'attack', 'threat', 'risk', 'danger', 'hazard',
    'warning', 'caution', 'alert', 'notice',
    'announcement', 'news', 'update', 'message',
    'email', 'letter', 'mail', 'post', 'chat',
    'conversation', 'discussion', 'comment',
    'reply', 'response', 'answer', 'solution',
    'resolution', 'fix', 'patch', 'update',
    'upgrade', 'improvement', 'enhancement',
    'feature', 'function', 'capability', 'ability',
    'skill', 'talent', 'strength', 'advantage',
    'benefit', 'gain', 'profit', 'reward',
    'bonus', 'prize', 'award', 'achievement',
    'accomplishment', 'success', 'victory',
    'win', 'progress', 'advance', 'development',
    'growth', 'expansion', 'extension',
    'increase', 'decrease', 'reduction',
    'compression', 'expansion', 'contraction',
    'addition', 'subtraction', 'multiplication',
    'division', 'calculation', 'computation',
    'operation', 'function', 'formula',
    'equation', 'expression', 'statement',
    'command', 'instruction', 'directive',
    'order', 'request', 'demand', 'requirement',
    'need', 'want', 'wish', 'desire', 'goal',
    'objective', 'target', 'aim', 'purpose',
    'intent', 'plan', 'strategy', 'tactic',
    'method', 'way', 'means', 'approach',
    'path', 'route', 'direction', 'course',
    'track', 'trail', 'trace', 'line', 'curve',
    'angle', 'circle', 'square', 'rectangle',
    'triangle', 'polygon', 'shape', 'form',
    'structure', 'framework', 'skeleton',
    'foundation', 'base', 'ground', 'bottom',
    'top', 'peak', 'summit', 'apex', 'vertex',
    'edge', 'side', 'face', 'surface', 'plane',
    'point', 'spot', 'dot', 'mark', 'line',
    'stroke', 'trace', 'trail', 'track',
    'imprint', 'impression', 'print', 'copy',
    'replica', 'reproduction', 'duplicate',
    'clone', 'mirror', 'reflection', 'image',
    'picture', 'photo', 'photograph', 'snapshot',
    'screenshot', 'capture', 'recording',
    'log', 'journal', 'diary', 'record',
    'history', 'chronicle', 'archive', 'file',
    'document', 'paper', 'report', 'account',
    'story', 'narrative', 'description',
    'explanation', 'definition', 'meaning',
    'sense', 'significance', 'importance',
    'relevance', 'pertinence', 'applicability',
    'usefulness', 'utility', 'value', 'worth',
    'merit', 'quality', 'standard', 'criterion',
    'measure', 'metric', 'indicator', 'sign',
    'signal', 'symbol', 'token', 'icon',
    'emblem', 'badge', 'logo', 'brand',
    'trademark', 'copyright', 'patent',
    'license', 'permit', 'authorization',
    'approval', 'acceptance', 'consent',
    'agreement', 'contract', 'deal',
    'transaction', 'exchange', 'trade',
    'commerce', 'business', 'market',
    'industry', 'sector', 'domain', 'field',
    'area', 'region', 'zone', 'territory',
    'land', 'country', 'nation', 'state',
    'province', 'city', 'town', 'village',
    'community', 'society', 'culture',
    'civilization', 'population', 'people',
    'person', 'individual', 'human', 'man',
    'woman', 'child', 'baby', 'adult',
    'youth', 'teenager', 'senior', 'elder',
    'citizen', 'resident', 'inhabitant',
    'occupant', 'tenant', 'owner', 'landlord',
    'guest', 'visitor', 'traveler', 'tourist',
    'passenger', 'commuter', 'pedestrian',
    'driver', 'rider', 'pilot', 'captain',
    'crew', 'staff', 'personnel', 'team',
    'crew', 'squad', 'unit', 'division',
    'department', 'section', 'branch',
    'office', 'bureau', 'agency', 'service',
    'facility', 'center', 'institute',
    'institution', 'organization',
    'association', 'union', 'club', 'group',
    'party', 'faction', 'wing', 'side',
    'team', 'crew', 'band', 'gang', 'crowd',
    'mob', 'herd', 'flock', 'pack', 'swarm',
    'school', 'shoal', 'colony', 'nest',
    'den', 'lair', 'burrow', 'hole', 'cave',
    'shelter', 'home', 'house', 'building',
    'structure', 'construction', 'edifice',
    'architecture', 'design', 'plan',
    'blueprint', 'scheme', 'layout',
    'arrangement', 'organization',
    'system', 'network', 'web', 'mesh',
    'grid', 'matrix', 'array', 'list',
    'sequence', 'series', 'chain', 'string',
    'strand', 'thread', 'fiber', 'filament',
    'wire', 'cable', 'cord', 'rope', 'line',
    'pipeline', 'conduit', 'channel',
    'passage', 'tunnel', 'bridge', 'road',
    'street', 'avenue', 'boulevard',
    'highway', 'freeway', 'motorway',
    'expressway', 'turnpike', 'tollway',
    'path', 'trail', 'track', 'lane',
    'alley', 'drive', 'way', 'route',
    'course', 'direction', 'bearing',
    'heading', 'orientation', 'position',
    'location', 'place', 'site', 'spot',
    'point', 'station', 'stop', 'terminal',
    'depot', 'hub', 'center', 'core',
    'heart', 'middle', 'center', 'focus',
    'focal', 'central', 'main', 'primary',
    'principal', 'chief', 'head', 'lead',
    'foremost', 'premier', 'first',
    'initial', 'opening', 'introductory',
    'preliminary', 'precursory',
    'preparatory', 'antecedent',
    'preceding', 'previous', 'prior',
    'earlier', 'former', 'past', 'old',
    'ancient', 'antique', 'vintage',
    'classic', 'traditional', 'conventional',
    'standard', 'normal', 'regular',
    'usual', 'typical', 'common',
    'ordinary', 'everyday', 'routine',
    'habitual', 'customary', 'familiar',
    'known', 'recognized', 'acknowledged',
    'accepted', 'approved', 'endorsed',
    'supported', 'backed', 'funded',
    'sponsored', 'financed', 'paid',
    'compensated', 'remunerated',
    'rewarded', 'reimbursed', 'refunded',
    'repaid', 'returned', 'restored',
    'replaced', 'substituted',
    'exchanged', 'swapped', 'traded',
    'bartered', 'negotiated', 'discussed',
    'talked', 'spoke', 'said', 'told',
    'stated', 'declared', 'announced',
    'proclaimed', 'pronounced', 'uttered',
    'voiced', 'expressed', 'articulated',
    'enunciated', 'verbalized',
    'communicated', 'conveyed',
    'transmitted', 'sent', 'delivered',
    'received', 'got', 'obtained',
    'acquired', 'gained', 'earned',
    'won', 'achieved', 'attained',
    'reached', 'arrived', 'came',
    'went', 'moved', 'traveled',
    'journeyed', 'voyaged', 'sailed',
    'flew', 'drove', 'rode', 'walked',
    'ran', 'raced', 'hurried',
    'rushed', 'dashed', 'darted',
    'sprinted', 'jogged', 'trooped',
    'marched', 'paraded', 'proceeded',
    'advanced', 'progressed', 'moved',
    'went', 'left', 'departed',
    'exited', 'withdrew', 'retreated',
    'returned', 'came back', 'reversed',
    'turned', 'rotated', 'spun',
    'twisted', 'wound', 'coiled',
    'curled', 'looped', 'circled',
    'orbited', 'revolved', 'rotated',
    'turned', 'swiveled', 'pivoted',
    'hinged', 'jointed', 'connected',
    'linked', 'joined', 'attached',
    'fastened', 'secured', 'tied',
    'bound', 'wrapped', 'enclosed',
    'encased', 'contained', 'held',
    'grasped', 'gripped', 'clutched',
    'clasped', 'clenched', 'squeezed',
    'pressed', 'pushed', 'pulled',
    'dragged', 'drew', 'hauled',
    'tugged', 'yanked', 'jerked',
    'twisted', 'wrenched', 'pried',
    'levered', 'forced', 'compelled',
    'coerced', 'pressured', 'urged',
    'encouraged', 'motivated',
    'inspired', 'stimulated',
    'provoked', 'incited', 'instigated',
    'triggered', 'sparked', 'ignited',
    'fired', 'lit', 'burned',
    'blazed', 'flared', 'glowed',
    'shined', 'gleamed', 'glimmered',
    'glittered', 'sparkled', 'twinkled',
    'flickered', 'flashed', 'blinked',
    'winked', 'nodded', 'gestured',
    'signaled', 'motioned', 'waved',
    'saluted', 'greeted', 'welcomed',
    'received', 'accepted', 'admitted',
    'allowed', 'permitted', 'let',
    'enabled', 'empowered', 'authorized',
    'licensed', 'certified', 'qualified',
    'trained', 'educated', 'instructed',
    'taught', 'coached', 'mentored',
    'guided', 'led', 'directed',
    'managed', 'supervised',
    'oversaw', 'monitored', 'watched',
    'observed', 'viewed', 'seen',
    'looked', 'gazed', 'stared',
    'glanced', 'peered', 'peeked',
    'eyed', 'regarded', 'considered',
    'contemplated', 'pondered',
    'reflected', 'thought', 'mused',
    'meditated', 'concentrated',
    'focused', 'attended', 'listened',
    'heard', 'sounded', 'rang',
    'tolled', 'pealed', 'chimed',
    'clanged', 'banged', 'knocked',
    'tapped', 'rapped', 'patted',
    'touched', 'felt', 'sensed',
    'perceived', 'noticed', 'detected',
    'discovered', 'found', 'located',
    'placed', 'positioned', 'situated',
    'settled', 'established', 'installed',
    'fitted', 'adapted', 'adjusted',
    'modified', 'altered', 'changed',
    'transformed', 'converted',
    'transmuted', 'transfigured',
    'metamorphosed', 'evolved',
    'developed', 'grown', 'matured',
    'aged', 'ripened', 'seasoned',
    'experienced', 'practiced',
    'skilled', 'expert', 'proficient',
    'accomplished', 'talented',
    'gifted', 'able', 'capable',
    'competent', 'qualified', 'fit',
    'suitable', 'appropriate',
    'proper', 'correct', 'right',
    'accurate', 'precise', 'exact',
    'specific', 'particular',
    'certain', 'sure', 'positive',
    'definite', 'absolute',
    'conclusive', 'final', 'ultimate',
    'last', 'eventual', 'prospective',
    'potential', 'possible', 'feasible',
    'viable', 'practical', 'workable',
    'achievable', 'attainable',
    'reachable', 'accessible',
    'available', 'obtainable',
    'procurable', 'acquirable',
    'gettable', 'fetchable',
    'retrievable', 'recoverable',
    'restorable', 'renewable',
    'replaceable', 'substitutable',
    'exchangeable', 'convertible',
    'transformable', 'changeable',
    'alterable', 'modifiable',
    'adjustable', 'adaptable',
    'flexible', 'pliable', 'pliant',
    'supple', 'elastic', 'resilient',
    'springy', 'bouncy', 'rubbery',
    'stretchy', 'extensible',
    'expandable', 'inflatable',
    'deflatable', 'collapsible',
    'foldable', 'packable', 'portable',
    'movable', 'mobile', 'transportable',
    'transferable', 'transmittable',
    'communicable', 'contagious',
    'infectious', 'catching',
    'spreading', 'diffusing',
    'dispersing', 'scattering',
    'distributing', 'allocating',
    'assigning', 'allotting',
    'apportioning', 'dividing',
    'splitting', 'separating',
    'parting', 'detaching',
    'disconnecting', 'unlinking',
    'unfastening', 'loosening',
    'untightening', 'relaxing',
    'slackening', 'easing',
    'softening', 'smoothing',
    'polishing', 'refining',
    'perfecting', 'completing',
    'finishing', 'concluding',
    'ending', 'terminating',
    'closing', 'shutting',
    'sealing', 'locking',
    'securing', 'protecting',
    'guarding', 'defending',
    'shielding', 'screening',
    'sheltering', 'harboring',
    'housing', 'accommodating',
    'lodging', 'quartering',
    'boarding', 'billeting',
    'camping', 'tenting',
    'picnicking', 'outing',
    'excursion', 'trip', 'tour',
    'voyage', 'cruise', 'sail',
    'flight', 'drive', 'ride',
    'hike', 'walk', 'stroll',
    'amble', 'saunter', 'wander',
    'roam', 'ramble', 'rove',
    'range', 'stray', 'drift',
    'float', 'glide', 'slide',
    'slip', 'skid', 'slither',
    'creep', 'crawl', 'climb',
    'scale', 'ascend', 'mount',
    'rise', 'arise', 'spring',
    'leap', 'jump', 'hop',
    'skip', 'bound', 'bounce',
    'rebound', 'ricochet',
    'deflect', 'deviate',
    'diverge', 'branch', 'fork',
    'split', 'divide', 'part',
    'separate', 'break', 'crack',
    'snap', 'split', 'tear',
    'rip', 'shred', 'cut',
    'slice', 'dice', 'chop',
    'mince', 'grind', 'crush',
    'smash', 'shatter', 'splinter',
    'fragment', 'crumble',
    'disintegrate', 'decay',
    'rot', 'decompose',
    'deteriorate', 'degrade',
    'decline', 'worsen',
    'deteriorate', 'degenerate',
    'retrograde', 'recede',
    'retreat', 'withdraw',
    'retire', 'resign', 'quit',
    'leave', 'depart', 'go',
    'exit', 'issue', 'emerge',
    'appear', 'arise', 'occur',
    'happen', 'transpire',
    'ensue', 'result', 'follow',
    'succeed', 'supersede',
    'replace', 'displace',
    'supplant', 'oust', 'eject',
    'expel', 'evict', 'remove',
    'eliminate', 'eradicate',
    'extirpate', 'exterminate',
    'annihilate', 'destroy',
    'demolish', 'raze', 'ruin',
    'wreck', 'devastate',
    'desolate', 'ravage',
    'pillage', 'plunder',
    'loot', 'sack', 'ransack',
    'strip', 'deprive',
    'divest', 'dispossess',
    'bereave', 'rob', 'steal',
    'thieve', 'pilfer',
    'filch', 'purloin',
    'embezzle', 'misappropriate',
    'peculate', 'defalcate',
    'abstract', 'extract',
    'derive', 'obtain', 'get',
    'acquire', 'procure',
    'secure', 'gain', 'earn',
    'win', 'achieve', 'attain',
    'reach', 'arrive', 'come',
}

# CSS class names, properties, and values to skip
CSS_SKIP = {
    'none', 'auto', 'inherit', 'initial', 'unset', 'transparent',
    'block', 'inline', 'flex', 'grid', 'table', 'contents',
    'absolute', 'relative', 'fixed', 'static', 'sticky',
    'hidden', 'visible', 'scroll', 'auto', 'clip',
    'left', 'right', 'center', 'top', 'bottom',
    'start', 'end', 'justify', 'space-between', 'space-around',
    'nowrap', 'wrap', 'wrap-reverse',
    'row', 'column', 'dense',
    'solid', 'dashed', 'dotted', 'double', 'groove', 'ridge', 'inset', 'outset',
    'bold', 'normal', 'lighter', 'bolder',
    'italic', 'oblique', 'normal',
    'uppercase', 'lowercase', 'capitalize',
    'pointer', 'default', 'not-allowed', 'wait', 'crosshair',
    'cover', 'contain', 'fill', 'scale-down',
    'ease', 'linear', 'ease-in', 'ease-out', 'ease-in-out',
    'forwards', 'backwards', 'both', 'infinite',
    'alternate', 'alternate-reverse',
    'running', 'paused',
    'all', 'color', 'background', 'opacity', 'transform',
    'width', 'height', 'margin', 'padding', 'border',
    'display', 'position', 'overflow', 'float', 'clear',
    'font', 'text', 'line', 'letter', 'word',
    'white-space', 'vertical-align', 'text-align',
    'cursor', 'z-index', 'opacity', 'visibility',
    'transform', 'transition', 'animation',
    'filter', 'clip', 'mask', 'mix-blend-mode',
    'isolation', 'contain', 'will-change',
    'grid', 'flex', 'order', 'align', 'justify',
    'gap', 'area', 'template', 'column', 'row',
    'span', 'dense', 'auto-flow',
    'min-content', 'max-content', 'fit-content',
    'minmax', 'repeat', 'fr',
    'true', 'false',
}

# Common HTML attributes and values
HTML_SKIP = {
    'text', 'password', 'email', 'number', 'tel', 'url',
    'search', 'date', 'time', 'datetime-local', 'month', 'week',
    'color', 'range', 'file', 'checkbox', 'radio', 'submit',
    'button', 'reset', 'image', 'hidden',
    'GET', 'POST', 'PUT', 'DELETE', 'PATCH',
    '_self', '_blank', '_parent', '_top',
    'multipart/form-data', 'application/x-www-form-urlencoded',
    'text/plain', 'text/html', 'text/css', 'text/javascript',
    'application/json', 'application/xml',
    'en', 'zh', 'zh-CN', 'zh-TW', 'ja', 'ko', 'fr', 'de', 'es',
    'utf-8', 'utf8', 'iso-8859-1',
    'sha256', 'sha384', 'sha512', 'md5',
    'anonymous', 'use-credentials',
    'lazy', 'eager', 'auto',
    'ltr', 'rtl', 'auto',
    'yes', 'no',
    'on', 'off',
    'form', 'list', 'dialog', 'search',
    'prev', 'next', 'help', 'bookmark',
    'nofollow', 'noopener', 'noreferrer',
    'icon', 'shortcut icon', 'apple-touch-icon',
}

# Common variable names and code patterns
CODE_SKIP = {
    'event', 'target', 'currentTarget', 'relatedTarget',
    'element', 'node', 'parent', 'child', 'children',
    'sibling', 'nextSibling', 'previousSibling',
    'firstChild', 'lastChild', 'parentNode',
    'document', 'window', 'console', 'navigator',
    'location', 'history', 'screen', 'localStorage',
    'sessionStorage', 'indexedDB', 'fetch',
    'XMLHttpRequest', 'WebSocket', 'EventSource',
    'Promise', 'Array', 'Object', 'String', 'Number',
    'Boolean', 'Date', 'Math', 'JSON', 'RegExp',
    'Error', 'TypeError', 'ReferenceError',
    'SyntaxError', 'RangeError', 'URIError',
    'Map', 'Set', 'WeakMap', 'WeakSet',
    'Proxy', 'Reflect', 'Symbol', 'Intl',
    'eval', 'parseInt', 'parseFloat', 'isNaN',
    'isFinite', 'encodeURI', 'decodeURI',
    'encodeURIComponent', 'decodeURIComponent',
    'escape', 'unescape', 'setTimeout',
    'setInterval', 'clearTimeout', 'clearInterval',
    'requestAnimationFrame', 'cancelAnimationFrame',
    'alert', 'confirm', 'prompt', 'open', 'close',
    'print', 'stop', 'focus', 'blur', 'scroll',
    'scrollTo', 'scrollBy', 'moveTo', 'moveBy',
    'resizeTo', 'resizeBy', 'find', 'match',
    'replace', 'search', 'split', 'slice',
    'substr', 'substring', 'concat', 'join',
    'push', 'pop', 'shift', 'unshift',
    'sort', 'reverse', 'fill', 'copyWithin',
    'splice', 'indexOf', 'lastIndexOf',
    'includes', 'findIndex', 'find',
    'filter', 'map', 'reduce', 'reduceRight',
    'every', 'some', 'forEach', 'flat',
    'flatMap', 'entries', 'keys', 'values',
    'from', 'of', 'isArray', 'length',
    'prototype', 'constructor', 'hasOwnProperty',
    'propertyIsEnumerable', 'toString',
    'valueOf', 'toLocaleString', 'bind',
    'call', 'apply', 'arguments', 'caller',
    'name', 'caller', 'arguments',
    'prototype', '__proto__', 'constructor',
    'get', 'set', 'defineProperty',
    'defineProperties', 'getOwnPropertyDescriptor',
    'getOwnPropertyDescriptors', 'getOwnPropertyNames',
    'getOwnPropertySymbols', 'preventExtensions',
    'isExtensible', 'isSealed', 'isFrozen',
    'seal', 'freeze', 'assign', 'create',
    'setPrototypeOf', 'getPrototypeOf',
    'keys', 'values', 'entries', 'fromEntries',
    'is', 'entries', 'keys', 'values',
    'addEventListener', 'removeEventListener',
    'dispatchEvent', 'createElement',
    'createTextNode', 'createComment',
    'createDocumentFragment', 'createRange',
    'createNodeIterator', 'createTreeWalker',
    'getElementById', 'getElementsByName',
    'getElementsByTagName', 'getElementsByClassName',
    'querySelector', 'querySelectorAll',
    'appendChild', 'removeChild', 'replaceChild',
    'insertBefore', 'cloneNode', 'normalize',
    'contains', 'compareDocumentPosition',
    'isEqualNode', 'isSameNode', 'hasChildNodes',
    'lookupPrefix', 'lookupNamespaceURI',
    'isDefaultNamespace', 'hasAttributes',
    'getAttribute', 'setAttribute', 'removeAttribute',
    'hasAttribute', 'getAttributeNode',
    'setAttributeNode', 'removeAttributeNode',
    'getAttributeNodeNS', 'setAttributeNodeNS',
    'removeAttributeNS', 'getAttributeNS',
    'hasAttributeNS', 'setAttributeNS',
    'getElementsByTagNameNS', 'createElementNS',
    'createAttributeNS', 'getNamedItem',
    'setNamedItem', 'removeNamedItem',
    'item', 'length', 'name', 'value',
    'specified', 'ownerElement', 'schemaTypeInfo',
    'isId', 'namespaceURI', 'prefix', 'localName',
    'tagName', 'id', 'className', 'classList',
    'innerHTML', 'outerHTML', 'textContent',
    'innerText', 'outerText', 'children',
    'firstElementChild', 'lastElementChild',
    'childElementCount', 'nextElementSibling',
    'previousElementSibling', 'clientHeight',
    'clientWidth', 'clientTop', 'clientLeft',
    'offsetHeight', 'offsetWidth', 'offsetTop',
    'offsetLeft', 'scrollHeight', 'scrollWidth',
    'scrollTop', 'scrollLeft', 'style',
    'dataset', 'attributes', 'ownerDocument',
    'parentNode', 'parentElement', 'childNodes',
    'firstChild', 'lastChild', 'previousSibling',
    'nextSibling', 'nodeName', 'nodeType',
    'nodeValue', 'baseURI', 'isConnected',
    'rootNode', 'isContentEditable',
    'contentEditable', 'dir', 'lang', 'title',
    'tabIndex', 'hidden', 'draggable',
    'spellcheck', 'translate', 'slot',
    'part', 'popover', 'command',
    'commandForElement',
    'offsetParent', 'offsetHeight', 'offsetWidth',
    'scrollIntoView', 'scrollIntoViewIfNeeded',
    'getClientRects', 'getBoundingClientRect',
    'scroll', 'scrollTo', 'scrollBy',
    'insertAdjacentElement', 'insertAdjacentHTML',
    'insertAdjacentText', 'before', 'after',
    'replaceWith', 'remove', 'prepend',
    'append', 'replaceChildren', 'attachShadow',
    'requestFullscreen', 'webkitRequestFullscreen',
    'requestPointerLock', 'animate',
    'getAnimations', 'computedStyleMap',
    'toggleAttribute', 'closest', 'matches',
    'webkitMatchesSelector', 'msMatchesSelector',
    'mozMatchesSelector', 'oMatchesSelector',
    'getAttributeNames', 'getElementById',
    'createEvent', 'createRange', 'createNodeIterator',
    'createTreeWalker', 'createProcessingInstruction',
    'importNode', 'adoptNode', 'implementation',
    'URL', 'documentURI', 'compatMode',
    'characterSet', 'charset', 'inputEncoding',
    'contentType', 'doctype', 'documentElement',
    'body', 'head', 'title', 'images',
    'embeds', 'plugins', 'links', 'forms',
    'scripts', 'anchors', 'applets',
    'all', 'domain', 'referrer', 'cookie',
    'lastModified', 'readyState', 'designMode',
    'fgColor', 'bgColor', 'linkColor',
    'vlinkColor', 'alinkColor', 'defaultView',
    'activeElement', 'onreadystatechange',
    'fullscreenEnabled', 'fullscreenElement',
    'onfullscreenchange', 'onfullscreenerror',
    'visibilityState', 'hidden', 'onvisibilitychange',
    'selectedStylesheetSet', 'preferredStylesheetSet',
    'styleSheets', 'fonts', 'rootElement',
    'children', 'firstElementChild',
    'lastElementChild', 'childElementCount',
}


def is_likely_user_text(text):
    """Return True if text looks like user-facing UI text."""
    text = text.strip()

    if len(text) < 2 or len(text) > 100:
        return False

    # Must contain English letters
    if not re.search(r'[a-zA-Z]{2,}', text):
        return False

    # Skip CSS values
    if text.lower() in CSS_SKIP:
        return False

    # Skip HTML attribute values
    if text.lower() in HTML_SKIP:
        return False

    # Skip code keywords
    if text in CODE_SKIP:
        return False

    # Skip if starts with common code patterns
    if text.startswith(('var ', 'const ', 'let ', 'function ', 'return ',
                        'if (', 'for (', 'while (', 'switch (',
                        'document.', 'window.', 'console.', 'Math.')):
        return False

    # Skip if looks like a CSS selector
    if re.match(r'^[.#][a-zA-Z_-][\w-]*$', text):
        return False

    # Skip if looks like a file path
    if re.match(r'^[/\.][\w/\.]', text) or '://' in text:
        return False

    # Skip if looks like a template expression
    if '${' in text or '{{' in text:
        return False

    # Skip if just camelCase or snake_case variable names
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', text):
        return False

    # Skip if just numbers and punctuation
    if not re.search(r'[a-zA-Z]', text):
        return False

    # Must contain at least one common English word to be user-facing
    text_lower = text.lower()
    words = set(re.findall(r'[a-zA-Z]+', text_lower))

    # If it has any user word, it's likely user-facing
    if words & USER_WORDS:
        return True

    # If it starts with a capital letter and has spaces, might be a title/label
    if text[0].isupper() and ' ' in text and len(text) > 3:
        return True

    # If it contains punctuation that suggests a sentence
    if any(c in text for c in '.,!?;:'):
        return True

    return False


def extract_template_strings(filepath):
    """Extract user-facing strings from HTML templates."""
    results = []
    content = filepath.read_text(encoding="utf-8")

    # Remove script and style blocks to avoid extracting code
    content_no_script = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content_no_style = re.sub(r'<style[^>]*>.*?</style>', '', content_no_script, flags=re.DOTALL | re.IGNORECASE)

    # Pattern 1: Text nodes
    for match in re.finditer(r'>([^<]{2,80})<', content_no_style):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "text_node",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 2: Specific attributes
    attr_patterns = [
        (r'placeholder=["\']([^"\']+)["\']', "placeholder"),
        (r'title=["\']([^"\']+)["\']', "title"),
        (r'aria-label=["\']([^"\']+)["\']', "aria-label"),
        (r'alt=["\']([^"\']+)["\']', "alt"),
    ]

    for pattern, attr_type in attr_patterns:
        for match in re.finditer(pattern, content):
            text = match.group(1).strip()
            if is_likely_user_text(text):
                # Skip if already contains Jinja2
                if '{{' in text or '{%' in text:
                    continue
                results.append({
                    "type": f"attr_{attr_type}",
                    "text": text,
                    "line": content[:match.start()].count('\n') + 1
                })

    return results


def extract_js_strings(filepath):
    """Extract user-facing strings from JS files."""
    results = []
    content = filepath.read_text(encoding="utf-8")

    # Skip i18n-aware files
    if filepath.name in SKIP_JS:
        return results

    # Pattern 1: alert/confirm/prompt strings
    for match in re.finditer(r'(?:alert|confirm|prompt)\s*\(\s*["\']([^"\']+)["\']\s*\)', content):
        text = match.group(1).strip()
        if len(text) >= 2:
            results.append({
                "type": "dialog",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 2: console messages that look user-facing
    for match in re.finditer(r'console\.(?:warn|error)\s*\(\s*["\']([^"\']{5,})["\']', content):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "console_message",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 3: textContent assignments with plain strings
    for match in re.finditer(r'\.(?:textContent|innerText)\s*=\s*["\']([^"\']{2,})["\']', content):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "dom_content",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 4: toast/notification calls
    for match in re.finditer(r'(?:showToast|showNotification|createToast)\s*\([^)]*["\']([^"\']{2,})["\']', content):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "toast",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 5: SafeLogger user-facing messages
    for match in re.finditer(r'SafeLogger\.(?:log|warn|error)\s*\(\s*["\']([^"\']{5,})["\']', content):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "logger",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    # Pattern 6: Error messages
    for match in re.finditer(r'(?:new\s+Error\s*\(\s*["\']|throw\s+new\s+Error\s*\(\s*["\'])([^"\']{3,})["\']', content):
        text = match.group(1).strip()
        if is_likely_user_text(text):
            results.append({
                "type": "error",
                "text": text,
                "line": content[:match.start()].count('\n') + 1
            })

    return results


def deduplicate(strings):
    seen = set()
    unique = []
    for s in strings:
        text = s["text"]
        if text not in seen:
            seen.add(text)
            unique.append(s)
    return unique


def main():
    all_data = {
        "templates": {},
        "js": {},
        "summary": {
            "total_template_strings": 0,
            "total_js_strings": 0,
            "unique_strings": 0
        }
    }

    unique_all = set()

    for filepath in sorted(TEMPLATES_DIR.rglob("*.html")):
        if filepath.name in SKIP_TEMPLATES:
            continue
        rel_path = filepath.relative_to(PROJECT_ROOT).as_posix()
        strings = extract_template_strings(filepath)
        if strings:
            strings = deduplicate(strings)
            all_data["templates"][rel_path] = strings
            all_data["summary"]["total_template_strings"] += len(strings)
            for s in strings:
                unique_all.add(s["text"])

    for filepath in sorted(JS_DIR.rglob("*.js")):
        if filepath.name in SKIP_JS:
            continue
        rel_path = filepath.relative_to(PROJECT_ROOT).as_posix()
        strings = extract_js_strings(filepath)
        if strings:
            strings = deduplicate(strings)
            all_data["js"][rel_path] = strings
            all_data["summary"]["total_js_strings"] += len(strings)
            for s in strings:
                unique_all.add(s["text"])

    all_data["summary"]["unique_strings"] = len(unique_all)
    all_data["unique_strings"] = sorted(unique_all)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)

    print(f"Extracted {len(unique_all)} unique strings:")
    print(f"  - {all_data['summary']['total_template_strings']} from templates")
    print(f"  - {all_data['summary']['total_js_strings']} from JS files")
    print(f"\nOutput: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
