import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch
from daon_user_api.internet_connector import WebsiteImportContext


class OpenLawMcpTests(unittest.TestCase):
    context=WebsiteImportContext('tenant','workspace-qa','notebook','actor','trace','policy')

    def client(self, **options):
        from daon_user_api.open_law_mcp_client import OpenLawMcpClient
        return OpenLawMcpClient(api_key='fixture-oc',enabled=True,qa_workspace_id='workspace-qa',
            authorize_context=lambda ctx:'frozen-policy',resolver=lambda *_:('8.8.8.8',),**options)

    def test_flag_off_and_other_workspace_no_egress(self):
        from daon_user_api.open_law_mcp_client import OpenLawMcpClient
        with patch('daon_user_api.open_law_mcp_client.subprocess.Popen') as spawn:
            for client,context in [(OpenLawMcpClient(api_key='fixture-oc'),self.context),
                (self.client(),replace(self.context,workspace_id='other')),
                (OpenLawMcpClient(api_key='fixture-oc',enabled=True,qa_workspace_id='workspace-qa'),self.context)]:
                with self.assertRaises(ValueError):client.search(context,'민법',1)
            spawn.assert_not_called()

    def test_real_stdio_initialize_tools_and_read(self):
        from test_open_law_client import body_bytes,search_bytes
        from daon_user_api.open_law_client import OpenLawSelection
        real_popen=subprocess.Popen;children=[]
        # Only official HTTP is replaced in the child; actual production
        # client and server exchange initialize/tools/call over real OS pipes.
        program=('from daon_user_api.open_law_client import OpenLawClient;'
            'from daon_user_api.open_law_mcp_server import serve;'
            f'raw={body_bytes()!r};listing={search_bytes()!r};'
            'serve(OpenLawClient(api_key="fixture-oc",resolver=lambda *_:("8.8.8.8",),'
            'transport=lambda path,*_:listing if path.endswith("lawSearch.do") else raw))')
        def spawn(argv,**kwargs):
            self.assertEqual(argv,[sys.executable,'-B','-m','daon_user_api.open_law_mcp_server'])
            self.assertIs(kwargs['shell'],False)
            self.assertNotIn('fixture-oc',' '.join(argv))
            self.assertNotIn('DAON_SECRET_UNRELATED',kwargs['env'])
            child=real_popen([sys.executable,'-B','-c',program],**kwargs);children.append(child);return child
        with patch.dict(os.environ,{'DAON_SECRET_UNRELATED':'must-not-cross'}),patch('daon_user_api.open_law_mcp_client.subprocess.Popen',side_effect=spawn):
            client=self.client();self.assertEqual(client.probe(self.context)['protocol_version'],'2025-11-25')
            document=client.read(self.context,OpenLawSelection('123','20260101',None))
            self.assertEqual(document.raw_content,body_bytes())
        self.assertTrue(children);self.assertTrue(all(c.poll() is not None for c in children))
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),
                    ('peak',ctypes.c_size_t),('working',ctypes.c_size_t),
                    ('peak_paged',ctypes.c_size_t),('paged',ctypes.c_size_t),
                    ('peak_nonpaged',ctypes.c_size_t),('nonpaged',ctypes.c_size_t),
                    ('pagefile',ctypes.c_size_t),('peak_pagefile',ctypes.c_size_t)]
            peaks=[]
            for child in children:
                counters=Counters();counters.cb=ctypes.sizeof(counters)
                self.assertTrue(ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(int(child._handle)),ctypes.byref(counters),counters.cb))
                self.assertGreater(counters.peak,0);peaks.append(counters.peak)
            print('MCP fixture child PeakWorkingSetSize bytes:',peaks)

    def test_wrong_protocol_id_and_unlisted_tool_rejected(self):
        from daon_user_api.open_law_mcp_server import dispatch
        from daon_user_api.open_law_client import OpenLawClient
        for message in [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'wrong'}},
            {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'execute','arguments':{}}}]:
            self.assertIn('error',dispatch(message,OpenLawClient(api_key=None),{}))

    def test_timeout_oversize_and_child_reaped(self):
        real_popen=subprocess.Popen
        for program in ['import time;time.sleep(10)','import sys;sys.stdout.buffer.write(b"x"*8388609);sys.stdout.flush()']:
            children=[]
            def spawn(_argv,**kwargs):
                child=real_popen([sys.executable,'-B','-c',program],**kwargs);children.append(child);return child
            with patch('daon_user_api.open_law_mcp_client.subprocess.Popen',side_effect=spawn):
                with self.assertRaises(ValueError):self.client(timeout_seconds=.1).search(self.context,'민법',1)
            self.assertTrue(all(c.poll() is not None for c in children))

    def test_parent_rejects_wrong_id_version_and_server_request(self):
        real_popen=subprocess.Popen
        for response in [
            {'jsonrpc':'2.0','id':99,'result':{}},
            {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}},
            {'jsonrpc':'2.0','id':1,'method':'sampling/createMessage','params':{}},
        ]:
            children=[]
            program=f'import sys;sys.stdin.readline();print({json.dumps(response)!r},flush=True)'
            def spawn(_argv,**kwargs):
                child=real_popen([sys.executable,'-B','-c',program],**kwargs);children.append(child);return child
            with patch('daon_user_api.open_law_mcp_client.subprocess.Popen',side_effect=spawn):
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):self.client().probe(self.context)
            self.assertTrue(all(c.poll() is not None for c in children))

    def test_revoked_policy_and_concurrency_limit_do_not_spawn(self):
        from daon_user_api.open_law_mcp_client import OpenLawMcpClient,_SLOTS
        def denied(_):raise RuntimeError('secret exception must not escape')
        with patch('daon_user_api.open_law_mcp_client.subprocess.Popen') as spawn:
            client=OpenLawMcpClient(api_key='fixture-oc',enabled=True,qa_workspace_id='workspace-qa',authorize_context=denied)
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_EGRESS_BLOCKED$'):client.probe(self.context)
            self.assertTrue(_SLOTS.acquire(False));self.assertTrue(_SLOTS.acquire(False))
            try:
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_BUSY$'):self.client().probe(self.context)
            finally:_SLOTS.release();_SLOTS.release()
            spawn.assert_not_called()

    def test_parent_resolves_before_mcp_child_and_server_never_starts_dns_child(self):
        from test_open_law_client import search_bytes
        from daon_user_api.open_law_mcp_client import OpenLawMcpClient
        real_popen=subprocess.Popen;children=[];events=[]
        # Real server default construction; HTTP fixture only. A nested DNS
        # attempt fails rather than making an accidental external request.
        program=('import daon_user_api.open_law_client as c;import daon_user_api.open_law_mcp_server as s;'
            'original=s.OpenLawClient;'
            'c.resolve_public_ips=lambda *_:(_ for _ in ()).throw(OSError("nested DNS forbidden"));'
            f's.OpenLawClient=lambda **kw:original(**kw,transport=lambda *_:{search_bytes()!r});s.serve()')
        def resolve(*_):events.append('dns-complete');return ('8.8.8.8',)
        def spawn(_argv,**kwargs):
            events.append('mcp-spawn');self.assertEqual(json.loads(kwargs['env']['DAON_OPEN_LAW_APPROVED_IPS']),['8.8.8.8'])
            child=real_popen([sys.executable,'-B','-c',program],**kwargs);children.append(child);return child
        client=OpenLawMcpClient(api_key='fixture-oc',enabled=True,qa_workspace_id='workspace-qa',authorize_context=lambda _:'policy',resolver=resolve)
        with patch('daon_user_api.open_law_mcp_client.subprocess.Popen',side_effect=spawn):
            self.assertEqual(client.search(self.context,'민법',1)['total_count'],1)
        self.assertEqual(events,['dns-complete','mcp-spawn']);self.assertTrue(all(c.poll() is not None for c in children))

    def test_dns_wait_timeout_reaps_parent_owned_resolver_before_mcp_launch(self):
        from daon_user_api.open_law_mcp_client import OpenLawMcpClient
        real_popen=subprocess.Popen;children=[]
        def spawn(argv,**kwargs):
            self.assertNotIn('-m',argv,'MCP must not start during DNS wait')
            child=real_popen([sys.executable,'-B','-c','import time;time.sleep(10)'],**kwargs)
            children.append(child);return child
        client=OpenLawMcpClient(api_key='fixture-oc',enabled=True,qa_workspace_id='workspace-qa',
            authorize_context=lambda _:'policy',timeout_seconds=.1)
        with patch('daon_user_api.safe_http_fetch.subprocess.Popen',side_effect=spawn):
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_TIMEOUT$'):client.probe(self.context)
        self.assertEqual(len(children),1);self.assertIsNotNone(children[0].poll())
