"""
backend/app/mock/fixtures.py
Golden fixtures and response generators for Google Gemini web mock server.
"""

from __future__ import annotations

import json
from typing import Any, List

# Standard WIZ_global_data extracted on gemini.google.com/app
WIZ_GLOBAL_DATA_GOLDEN = {
    "SNlM0e": "AIzaSyMockSNlM0eToken_2026_TestFixture_12345:1729144800",
    "cfb2h": "boq_assistant-bard-web-server_20261017.00_p0",
    "FdrFJe": "9876543210123456789",
    "TuX5cc": "en",
    "qKIAYe": "feeds/mock_push_feed_alpha_999",
}

HTML_APP_PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Gemini</title>
<script nonce="mock_nonce_1234">
window.WIZ_global_data = {json.dumps(WIZ_GLOBAL_DATA_GOLDEN)};
</script>
</head>
<body>
<div id="app">Gemini Mock Web Application</div>
</body>
</html>"""

HTML_APP_PAGE_GUEST = """<!doctype html>
<html lang="en">
<head>
<script nonce="mock_nonce_guest">
window.WIZ_global_data = {
  "cfb2h": "boq_assistant-bard-web-server_20261017.00_p0",
  "TuX5cc": "en"
};
</script>
</head>
<body>Guest Mode</body>
</html>"""

HTML_APP_PAGE_MALFORMED = """<!doctype html>
<html><head><script>
window.WIZ_global_data = { "unclosed": "syntax error
</script></head><body>Malformed</body></html>"""


def format_batchexecute_envelope(rpc_id: str, payload_obj: Any) -> str:
    """
    Encodes an RPC payload object into Google's batchexecute wire format:
    )]}'\n
    \n<length>\n[[["wrb.fr", "<rpc_id>", "<stringified_json_payload>", null, null, null, "generic"]]]\n
    """
    stringified_payload = json.dumps(payload_obj, separators=(",", ":"))
    envelope_inner = [
        [
            [
                "wrb.fr",
                rpc_id,
                stringified_payload,
                None,
                None,
                None,
                "generic",
            ]
        ]
    ]
    envelope_str = json.dumps(envelope_inner, separators=(",", ":"))
    # Length in UTF-16 code units (1 code unit = 2 bytes in UTF-16LE/BE)
    length = len(envelope_str.encode("utf-16le")) // 2
    return f")]}}'\n\n{length}\n{envelope_str}\n"



# RPC otAQ7b: GET_USER_STATUS
PAYLOAD_OTAQ7B_USER_STATUS = [
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    [
        # Available models list in [15]
        ["gemini-2.5-flash", "Gemini 2.5 Flash", "Fast, multimodal model for high-frequency tasks", 1, 1],
        ["gemini-2.5-pro", "Gemini 2.5 Pro", "Advanced reasoning and complex problem solving", 2, 1],
        ["gemini-2.0-flash-thinking", "Gemini 2.0 Flash Thinking", "Model with explicit reasoning and thought chain", 1, 2],
        ["gemini-ultra", "Gemini Ultra", "Maximum capability enterprise model", 3, 1],
    ],
    [1, 1],        # [16] Account tier: [is_pro_subscriber, is_authorized]
    [12, 1, 0, 1], # [17] Capability flags: capacity_tail = 12
]

# RPC MaZiqc: LIST_CONVERSATIONS
PAYLOAD_MAZIQC_CONVERSATIONS = [
    [
        ["c_mock_conv_001", "Architecture Review of Local Gemini Client", 1729140000000, 0, False],
        ["c_mock_conv_002", "Python Concurrency & Asyncio Best Practices", 1729135000000, 1, True],
        ["c_mock_conv_003", "SQLite FTS5 Query Optimization Guide", 1729130000000, 0, False],
    ],
    "mock_page_token_next_page",
    1,
]

# RPC hNvQHb: LIST_CONVERSATION_TURNS
PAYLOAD_HNVQHB_TURNS = [
    "c_mock_conv_001",
    [
        [
            "turn_u1",
            "user",
            [["text", "How do I implement DPAPI in Python?"]],
            1729140010000,
        ],
        [
            "turn_a1",
            "model",
            [["text", "Use `ctypes.windll.crypt32.CryptProtectData`."]],
            1729140025000,
            "r_mock_resp_001",
            "rc_mock_cand_001",
        ],
    ],
]

# RPC CNgdBe: LIST_GEMS
PAYLOAD_CNGDBE_GEMS = [
    [
        [
            "gem_code_architect",
            ["Code Architect", "Senior Principal Software Architect persona"],
            ["You are an expert software architect specializing in distributed systems and Python.", 1, 0],
            1729000000000,
            None,
            "https://lh3.googleusercontent.com/mock_gem_icon_1",
        ],
        [
            "gem_security_auditor",
            ["Security Auditor", "AppSec and vulnerability research persona"],
            ["You are a defensive application security engineer conducting code review.", 1, 0],
            1729005000000,
            None,
            "https://lh3.googleusercontent.com/mock_gem_icon_2",
        ],
    ],
    "gem_sync_token_123",
]

# RPC Code 7: PERMISSION_DENIED
PAYLOAD_CODE_7_PERMISSION_DENIED = [
    7,
    "Request had invalid authentication credentials. Expected OAuth 2 access token, login cookie or other valid credential.",
    [["@type", "type.googleapis.com/google.rpc.ErrorInfo", "reason", "CREDENTIALS_EXPIRED"]],
]


def format_stream_frame(part_json_data: Any) -> bytes:
    """
    Encodes an inner part_json structure into a length-prefixed streaming frame:
    \n\n<utf16_length>\n[[["wrb.fr","assistant.lamda.BardFrontendService/StreamGenerate","<escaped_json>"]]]\n\n
    """
    inner_str = json.dumps(part_json_data, separators=(",", ":"))
    outer = [[["wrb.fr", "assistant.lamda.BardFrontendService/StreamGenerate", inner_str]]]
    outer_str = json.dumps(outer, separators=(",", ":"))
    utf16_len = len(outer_str.encode("utf-16le")) // 2
    return f"\n\n{utf16_len}\n{outer_str}\n\n".encode("utf-8")


def build_golden_stream_sequence(
    conv_id: str = "c_mock_conv_001",
    resp_id: str = "r_mock_resp_001",
    cand_id: str = "rc_mock_cand_001",
) -> List[bytes]:
    """
    Builds a realistic streaming sequence:
    Frame 0: Anti-XSSI prefix
    Frame 1: Thought Process Start
    Frame 2: Thought Process Continuation
    Frame 3: Generated Text First Chunk
    Frame 4: Generated Text + Code Block
    Frame 5: Final Completion with Citations + Metadata + Status 2
    """
    chunks: List[bytes] = [b")]}'\n"]

    # Frame 1: Thought Start
    part1 = [
        None,
        [conv_id, resp_id, cand_id],
        None,
        None,
        [
            [
                cand_id,
                [""],
                None,
                None,
                None,
                None,
                None,
                None,
                [0],
                None, None, None, None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                [["Analyzing the user's query regarding cryptographic vaults and local isolation..."]],
            ]
        ],
    ]
    chunks.append(format_stream_frame(part1))

    # Frame 2: Thought Continuation
    part2 = [
        None,
        [conv_id, resp_id, cand_id],
        None,
        None,
        [
            [
                cand_id,
                [""],
                None,
                None,
                None,
                None,
                None,
                None,
                [0],
                None, None, None, None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                [["Analyzing the user's query regarding cryptographic vaults and local isolation...\nFormulating solution using Windows DPAPI via ctypes and AES-GCM fallback."]],
            ]
        ],
    ]
    chunks.append(format_stream_frame(part2))

    # Frame 3: First text tokens
    part3 = [
        None,
        [conv_id, resp_id, cand_id],
        None,
        None,
        [
            [
                cand_id,
                ["To protect credentials locally, use Windows **DPAPI**."],
                None,
                None,
                None,
                None,
                None,
                None,
                [0],
            ]
        ],
    ]
    chunks.append(format_stream_frame(part3))

    # Frame 4: Cumulative text + Code block start
    part4 = [
        None,
        [conv_id, resp_id, cand_id],
        None,
        None,
        [
            [
                cand_id,
                ["To protect credentials locally, use Windows **DPAPI**.\n\nHere is the implementation:\n```python\nimport ctypes\n"],
                None,
                None,
                None,
                None,
                None,
                None,
                [0],
            ]
        ],
    ]
    chunks.append(format_stream_frame(part4))

    # Frame 5: Code block completed + text finished + completion status (2) + citations
    full_text = (
        "To protect credentials locally, use Windows **DPAPI**.\n\n"
        "Here is the implementation:\n"
        "```python\n"
        "import ctypes\n"
        "class DATA_BLOB(ctypes.Structure):\n"
        "    _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.POINTER(ctypes.c_byte))]\n"
        "```\n"
        "This binds encryption directly to the logged-in user profile."
    )
    part5 = [
        None,
        [conv_id, resp_id, cand_id],
        None,
        None,
        [
            [
                cand_id,
                [full_text],
                None,
                None,
                None,
                None,
                None,
                None,
                [2],  # Status 2 = Generation completed
                None,
                None,
                None,
                {  # Rich content block candidate_data[12]
                    "43": [  # Citations (Field 43)
                        [1, "https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata", "Microsoft DPAPI Documentation"]
                    ]
                },
            ]
        ],
        None, None, None, None, None, None, None, None, None, None,
        None, None, None, None, None, None, None, None, None, None,
        "mock_continuation_token_turn_2",  # part_json[25] Context continuation token
    ]
    chunks.append(format_stream_frame(part5))

    return chunks


def build_stream_error_frame(error_code: int, error_message: str) -> bytes:
    """
    Constructs an in-stream fatal error frame matching Google Gemini's protocol.
    Error codes:
      1013: Transient backend overload
      1037: Usage limit exceeded (hourly/daily quota)
      1050: Model inconsistent across turns
      1060: IP temporarily blocked
    """
    error_payload = [
        None,
        None,
        None,
        None,
        None,
        [
            None,
            None,
            [
                [
                    None,
                    [
                        error_code,
                        error_message,
                        {"reset_timestamp": 1729148400} if error_code == 1037 else {},
                    ],
                ]
            ],
        ],
    ]
    return format_stream_frame(error_payload)


# NotebookLM Golden Fixtures (Feature F49)
WIZ_GLOBAL_DATA_NOTEBOOKLM = {
    "SNlM0e": "AIzaSyMockNotebookLMSNlM0e_2026_TestFixture_99999:1729180000",
    "cfb2h": "boq_labs-tailwind-frontend_20261017.00_p0",
    "FdrFJe": "1234567890987654321",
    "TuX5cc": "en",
    "qKIAYe": "feeds/notebooklm_push_feed_999",
}

HTML_NOTEBOOKLM_APP_PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NotebookLM</title>
<script nonce="mock_nonce_nb_1234">
window.WIZ_global_data = {json.dumps(WIZ_GLOBAL_DATA_NOTEBOOKLM)};
</script>
</head>
<body>
<div id="app">NotebookLM Mock Web Application</div>
</body>
</html>"""

PAYLOAD_NOTEBOOKLM_LIST_NOTEBOOKS = [
    [
        [
            "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
            "Quantum Computing & Shor's Algorithm",
            [1729180000, 123000000],
            [1729184500, 456000000],
            None,
            2,
            "🧪",
        ],
        [
            "nb_1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "Rust Concurrency & Memory Safety",
            [1729100000, 0],
            [1729175000, 0],
            None,
            1,
            "🦀",
        ],
    ]
]

PAYLOAD_NOTEBOOKLM_GET_NOTEBOOK = [
    [
        "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
        "Quantum Computing & Shor's Algorithm",
        [1729180000, 0],
        [1729184500, 0],
    ],
    [
        [
            "src_qcomp_01",
            "Introduction to Quantum Algorithms.pdf",
            3,
            "https://arxiv.org/pdf/quant-ph/0001001.pdf",
            "A comprehensive primer on quantum circuits, superposition, phase estimation, and quantum Fourier transform.",
            14250,
            [1729180500, 0],
        ],
        [
            "src_qcomp_02",
            "Shor Algorithm Walkthrough Notes",
            1,
            None,
            "Detailed breakdown of order finding, modular exponentiation, and period detection using QFT.",
            3420,
            [1729181200, 0],
        ],
    ],
    [
        [
            "note_qcomp_n1",
            "Open Research Questions",
            "1. Physical qubit fidelity vs logical error rates\n2. Surface code syndrome measurement bottlenecks",
            [1729182000, 0],
            [1729183000, 0],
        ]
    ],
]

PAYLOAD_NOTEBOOKLM_CREATE_NOTEBOOK = [
    "nb_created_mock_uuid_12345",
    "New Test Notebook",
    [1729185000, 0],
]

PAYLOAD_NOTEBOOKLM_DELETE_NOTEBOOK = [True]

PAYLOAD_NOTEBOOKLM_RENAME_NOTEBOOK = [
    "nb_8a9b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
    "Renamed Notebook Title",
]

PAYLOAD_NOTEBOOKLM_ADD_SOURCE = [
    "src_added_mock_999",
    "Exported Research Transcript",
    1,
    None,
    "Source summary text exported from Gemini chat turn.",
    1500,
    [1729186000, 0],
]

