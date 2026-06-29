import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import goAIPM


def fake_client():
    client = Mock()
    client.credentials = {
        'auth_feishuMSG-xls': {'user_access_token': 'token'},
        'zsxq': {'access_token': 'zsxq-token'},
    }
    return client


class TowikiOutputTests(unittest.TestCase):
    def test_docling_proxy_preflight_detects_bare_ipv6(self):
        with patch.dict(
            os.environ,
            {'NO_PROXY': '127.0.0.1,localhost,::1,::1/128'},
            clear=False,
        ):
            issue = goAIPM._towiki_docling_proxy_issue()

        self.assertIn('NO_PROXY', issue)
        self.assertIn('::1', issue)

    def test_pymupdf_document_is_closed_after_extraction(self):
        document = Mock()
        document.__iter__ = Mock(return_value=iter([]))
        fitz = Mock()
        fitz.open.return_value = document

        with patch.dict(sys.modules, {'fitz': fitz}):
            result = goAIPM._towiki_pdf_data_with_pymupdf('/tmp/example.pdf')

        self.assertEqual({'pages': []}, result)
        document.close.assert_called_once_with()

    def test_pymupdf_fallback_reports_success(self):
        native_data = {'pages': [{'lines': [], 'items': [], 'images': []}]}

        with tempfile.NamedTemporaryFile(suffix='.pdf') as pdf:
            with patch.object(goAIPM, '_towiki_pdf_data_with_pymupdf',
                              return_value=native_data):
                with patch.object(goAIPM, '_towiki_pdf_blocks_with_docling',
                                  return_value=None):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        blocks = goAIPM._towiki_pdf_blocks(pdf.name)

        self.assertTrue(blocks)
        self.assertIn('PyMuPDF 回退解析成功', output.getvalue())

    def test_permission_failure_is_not_treated_as_style_downgrade(self):
        client = fake_client()
        response = Mock()
        response.status_code = 403
        response.json.return_value = {'code': 99991663, 'msg': 'forbidden'}
        client._session.post.return_value = response

        with patch.object(goAIPM, '_towiki_get_document_root',
                          return_value='root-id'):
            with self.assertRaisesRegex(RuntimeError, '权限失败'):
                goAIPM._towiki_append_blocks(
                    client, 'doc-id', [{'block_type': 2, 'text': {}}])

        self.assertEqual(1, client._session.post.call_count)

    def test_missing_user_token_reports_authorization_action(self):
        client = fake_client()
        client.credentials['auth_feishuMSG-xls']['user_access_token'] = ''

        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = goAIPM.process_towiki(
                client, 'https://example.com/article', 'wiki-url')

        text = output.getvalue()
        self.assertFalse(result)
        self.assertIn('缺少飞书 user_access_token', text)
        self.assertIn('python src/modules/feishu_auth.py', text)
        self.assertIn('目标文档未修改', text)

    def test_source_failure_does_not_report_partial_target_write(self):
        client = fake_client()

        with patch.object(goAIPM, '_towiki_resolve_doc_id', return_value='doc-id'):
            with patch.object(
                goAIPM,
                '_towiki_fetch_html_blocks',
                side_effect=requests.ConnectionError('connection reset'),
            ):
                with patch.object(goAIPM, '_towiki_clear_document') as clear:
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        result = goAIPM.process_towiki(
                            client, 'https://example.com/article', 'wiki-url')

        text = output.getvalue()
        self.assertFalse(result)
        clear.assert_not_called()
        self.assertIn('源内容读取最终失败', text)
        self.assertIn('目标文档尚未清空或写入', text)
        self.assertNotIn('目标文档可能只写入了部分内容', text)

    def test_html_source_read_reports_retry_recovery(self):
        client = fake_client()
        response = Mock()
        response.text = (
            '<html><head><title>Example</title></head>'
            '<body><article><p>' + ('content ' * 30) + '</p></article></body></html>'
        )
        response.raise_for_status.return_value = None
        client._session.get.side_effect = [
            requests.ReadTimeout('read timed out'),
            response,
        ]

        with patch.object(goAIPM.time, 'sleep'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                blocks = goAIPM._towiki_fetch_html_blocks(
                    client, 'https://example.com/article')

        self.assertTrue(blocks)
        self.assertIn('源网页读取失败（尝试 1/3）', output.getvalue())
        self.assertIn('源网页读取重试成功（尝试 2/3）', output.getvalue())

    def test_transient_write_failure_restarts_complete_document(self):
        client = fake_client()
        blocks = [{'block_type': 2, 'text': {}}]

        with patch.object(goAIPM, '_towiki_resolve_doc_id', return_value='doc-id'):
            with patch.object(goAIPM, '_towiki_fetch_html_blocks',
                              return_value=blocks):
                with patch.object(goAIPM, '_towiki_clear_document') as clear:
                    with patch.object(
                        goAIPM,
                        '_towiki_append_blocks',
                        side_effect=[requests.ReadTimeout('read timed out'), None],
                    ) as append:
                        with patch.object(goAIPM.time, 'sleep'):
                            with contextlib.redirect_stdout(io.StringIO()) as output:
                                result = goAIPM.process_towiki(
                                    client, 'https://example.com/article', 'wiki-url')

        text = output.getvalue()
        self.assertTrue(result)
        self.assertEqual(2, clear.call_count)
        self.assertEqual(2, append.call_count)
        self.assertIn('整份写入失败（尝试 1/3）', text)
        self.assertIn('将重新清空并从头写入', text)
        self.assertIn('整份文档重试成功（尝试 2/3）', text)
        self.assertIn('目标文档写入完成', text)

    def test_final_write_failure_warns_about_partial_document(self):
        client = fake_client()

        with patch.object(goAIPM, '_towiki_resolve_doc_id', return_value='doc-id'):
            with patch.object(goAIPM, '_towiki_fetch_html_blocks',
                              return_value=[{'block_type': 2}]):
                with patch.object(goAIPM, '_towiki_clear_document'):
                    with patch.object(
                        goAIPM,
                        '_towiki_append_blocks',
                        side_effect=requests.ReadTimeout('read timed out'),
                    ):
                        with patch.object(goAIPM.time, 'sleep'):
                            with contextlib.redirect_stdout(io.StringIO()) as output:
                                result = goAIPM.process_towiki(
                                    client, 'https://example.com/article', 'wiki-url')

        text = output.getvalue()
        self.assertFalse(result)
        self.assertIn('整份写入最终失败', text)
        self.assertIn('目标文档可能只写入了部分内容', text)

    def test_image_download_reports_retry_recovery(self):
        client = fake_client()
        response = Mock()
        response.headers = {'Content-Type': 'image/png'}
        response.content = b'image'
        response.raise_for_status.return_value = None
        client._session.get.side_effect = [
            requests.exceptions.ChunkedEncodingError('incomplete read'),
            response,
        ]

        with patch.object(goAIPM.time, 'sleep'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                image = goAIPM._towiki_download_image(
                    client,
                    {'source': 'https://example.com/image.png', 'base_url': ''},
                )

        self.assertEqual(b'image', image[0])
        self.assertIn('图片下载失败（尝试 1/3）', output.getvalue())
        self.assertIn('图片下载重试成功（尝试 2/3）', output.getvalue())


if __name__ == '__main__':
    unittest.main()
