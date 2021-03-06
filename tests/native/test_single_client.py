# This file is part of parallel-ssh.
#
# Copyright (C) 2014-2020 Panos Kittenis
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation, version 2.1.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import unittest
import os
import time
import subprocess
import shutil
from hashlib import sha256
from datetime import datetime

from gevent import socket, sleep, spawn, Timeout as GTimeout

from pssh.clients.native import SSHClient
from ssh2.session import Session
from ssh2.channel import Channel
from ssh2.exceptions import SocketDisconnectError, BannerRecvError, SocketRecvError, \
    AgentConnectionError, AgentListIdentitiesError, \
    AgentAuthenticationError, AgentGetIdentityError
from pssh.exceptions import AuthenticationException, ConnectionErrorException, \
    SessionError, SFTPIOError, SFTPError, SCPError, PKeyFileError, Timeout

from .base_ssh2_case import SSH2TestCase
from ..embedded_server.openssh import OpenSSHServer


class SSH2ClientTest(SSH2TestCase):

    def test_context_manager(self):
        with SSHClient(self.host, port=self.port,
                       pkey=self.user_key,
                       num_retries=1) as client:
            self.assertIsInstance(client, SSHClient)

    def test_sftp_fail(self):
        sftp = self.client._make_sftp()
        self.assertRaises(SFTPIOError, self.client._mkdir, sftp, '/blah')
        self.assertRaises(SFTPError, self.client.sftp_put, sftp, 'a file', '/blah')

    def test_scp_fail(self):
        self.assertRaises(SCPError, self.client.scp_recv, 'fakey', 'fake')
        try:
            os.mkdir('adir')
        except OSError:
            pass
        try:
            self.assertRaises(ValueError, self.client.scp_send, 'adir', 'fake')
        finally:
            os.rmdir('adir')

    def test_execute(self):
        host_out = self.client.run_command(self.cmd)
        output = list(host_out.stdout)
        stderr = list(host_out.stderr)
        expected = [self.resp]
        exit_code = host_out.channel.get_exit_status()
        self.assertEqual(host_out.exit_code, 0)
        self.assertEqual(expected, output)

    def test_open_session_timeout(self):
        client = SSHClient(self.host, port=self.port,
                           pkey=self.user_key,
                           num_retries=1,
                           timeout=1)
        def _session(timeout=2):
            sleep(2)
        client.open_session = _session
        self.assertRaises(GTimeout, client.run_command, self.cmd)

    def test_finished_error(self):
        self.assertRaises(ValueError, self.client.wait_finished, None)
        self.assertIsNone(self.client.finished(None))

    def test_stderr(self):
        host_out = self.client.run_command('echo "me" >&2')
        self.client.wait_finished(host_out)
        output = list(host_out.stdout)
        stderr = list(host_out.stderr)
        expected = ['me']
        self.assertListEqual(expected, stderr)
        self.assertTrue(len(output) == 0)

    def test_stdin(self):
        host_out = self.client.run_command('read line; echo $line')
        host_out.stdin.write('a line\n')
        host_out.stdin.flush()
        self.client.wait_finished(host_out)
        stdout = list(host_out.stdout)
        self.assertListEqual(stdout, ['a line'])

    def test_long_running_cmd(self):
        host_out = self.client.run_command('sleep 2; exit 2')
        self.assertRaises(ValueError, self.client.wait_finished, host_out.channel)
        self.client.wait_finished(host_out)
        exit_code = host_out.exit_code
        self.assertEqual(exit_code, 2)

    def test_manual_auth(self):
        client = SSHClient(self.host, port=self.port,
                           pkey=self.user_key,
                           num_retries=1,
                           allow_agent=False)
        client.session.disconnect()
        del client.session
        del client.sock
        client.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client._connect(self.host, self.port)
        client._init_session()
        # Identity auth
        client.pkey = None
        client.session.disconnect()
        del client.session
        del client.sock
        client.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client._connect(self.host, self.port)
        client.session = Session()
        client.session.handshake(client.sock)
        self.assertRaises(AuthenticationException, client.auth)

    def test_failed_auth(self):
        self.assertRaises(PKeyFileError, SSHClient, self.host, port=self.port,
                          pkey='client_pkey',
                          num_retries=1)
        self.assertRaises(PKeyFileError, SSHClient, self.host, port=self.port,
                          pkey='~/fake_key',
                          num_retries=1)

    def test_handshake_fail(self):
        client = SSHClient(self.host, port=self.port,
                           pkey=self.user_key,
                           num_retries=1)
        client.session.disconnect()
        self.assertRaises((SocketDisconnectError, BannerRecvError, SocketRecvError), client._init_session)

    def test_stdout_parsing(self):
        dir_list = os.listdir(os.path.expanduser('~'))
        host_out = self.client.run_command('ls -la')
        output = list(host_out.stdout)
        # Output of `ls` will have 'total', '.', and '..' in addition to dir
        # listing
        self.assertEqual(len(dir_list), len(output) - 3)

    def test_file_output_parsing(self):
        lines = int(subprocess.check_output(
            ['wc', '-l', 'README.rst']).split()[0])
        dir_name = os.path.dirname(__file__)
        _file = os.sep.join((dir_name, '..', '..', 'README.rst'))
        cmd = 'cat %s' % _file
        host_out = self.client.run_command(cmd)
        output = list(host_out.stdout)
        self.assertEqual(lines, len(output))

    def test_identity_auth_failure(self):
        self.assertRaises(AuthenticationException,
                          SSHClient, self.host, port=self.port, num_retries=1,
                          allow_agent=False)

    @unittest.skipUnless(bool(os.getenv('TRAVIS')),
                         "Not on Travis-CI - skipping agent auth failure test")
    def test_agent_auth_failure(self):
        self.assertRaises(AuthenticationException,
                          SSHClient, self.host, port=self.port, num_retries=1,
                          allow_agent=True)

    def test_password_auth_failure(self):
        self.assertRaises(AuthenticationException,
                          SSHClient, self.host, port=self.port, num_retries=1,
                          allow_agent=False,
                          password='blah blah blah')

    def test_retry_failure(self):
        self.assertRaises(ConnectionErrorException,
                          SSHClient, self.host, port=12345,
                          num_retries=2, _auth_thread_pool=False)

    def test_auth_retry_failure(self):
        self.assertRaises(AuthenticationException,
                          SSHClient, self.host, port=self.port,
                          user=self.user,
                          password='fake',
                          num_retries=3,
                          allow_agent=False)

    def test_connection_timeout(self):
        cmd = spawn(SSHClient, 'fakehost.com', port=12345,
                    num_retries=1, timeout=1, _auth_thread_pool=False)
        # Should fail within greenlet timeout, otherwise greenlet will
        # raise timeout which will fail the test
        self.assertRaises(ConnectionErrorException, cmd.get, timeout=2)

    def test_client_read_timeout(self):
        client = SSHClient(self.host, port=self.port,
                           pkey=self.user_key,
                           num_retries=1)
        host_out = client.run_command('sleep 2; echo me', timeout=0.2)
        self.assertRaises(Timeout, list, host_out.stdout)

    def test_multiple_clients_exec_terminates_channels(self):
        # See #200 - Multiple clients should not interfere with
        # each other. session.disconnect can leave state in libssh2
        # and break subsequent sessions even on different socket and
        # session
        def scope_killer():
            for _ in range(5):
                client = SSHClient(self.host, port=self.port,
                                   pkey=self.user_key,
                                   num_retries=1,
                                   allow_agent=False)
                host_out = client.run_command(self.cmd)
                output = list(host_out.stdout)
                self.assertListEqual(output, [self.resp])
                client.disconnect()
        scope_killer()

    def test_agent_auth_exceptions(self):
        """Test SSH agent authentication failure with custom client that
        does not do auth at class init.
        """
        class _SSHClient(SSHClient):
            def __init__(self, host, port, num_retries):
                self.keepalive_seconds = None
                super(SSHClient, self).__init__(
                    host, port=port, num_retries=2,
                    allow_agent=True)

            def _init_session(self):
                self.session = Session()
                if self.timeout:
                    self.session.set_timeout(self.timeout * 1000)
                self.session.handshake(self.sock)

            def _auth_retry(self):
                pass

        client = _SSHClient(self.host, port=self.port,
                           num_retries=1)
        self.assertRaises((AgentConnectionError, AgentListIdentitiesError, \
                           AgentAuthenticationError, AgentGetIdentityError),
                          client.session.agent_auth, client.user)
        self.assertRaises(AuthenticationException,
                          client.auth)

    def test_finished(self):
        self.assertFalse(self.client.finished(None))
        host_out = self.client.run_command('echo me')
        channel = host_out.channel
        self.assertFalse(self.client.finished(channel))
        self.assertRaises(ValueError, self.client.wait_finished, host_out.channel)
        self.client.wait_finished(host_out)
        stdout = list(host_out.stdout)
        self.assertTrue(self.client.finished(channel))
        self.assertListEqual(stdout, [self.resp])

    def test_wait_finished_timeout(self):
        host_out = self.client.run_command('sleep 2')
        timeout = 1
        self.assertFalse(self.client.finished(host_out.channel))
        start = datetime.now()
        self.assertRaises(Timeout, self.client.wait_finished, host_out, timeout=timeout)
        dt = datetime.now() - start
        self.assertTrue(timeout*1.05 > dt.total_seconds() > timeout)
        self.client.wait_finished(host_out)
        self.assertTrue(self.client.finished(host_out.channel))

    def test_scp_abspath_recursion(self):
        cur_dir = os.path.dirname(__file__)
        dir_name_to_copy = 'a_dir'
        files = ['file1', 'file2']
        dir_paths = [cur_dir, dir_name_to_copy]
        to_copy_dir_path = os.path.abspath(os.path.sep.join(dir_paths))
        # Dir to copy to
        copy_to_path = '/tmp/copied_dir'
        try:
            shutil.rmtree(copy_to_path)
        except Exception:
            pass
        try:
            try:
                os.makedirs(to_copy_dir_path)
            except OSError:
                pass
            # Copy for empty remote dir should create local dir
            self.client.scp_recv(to_copy_dir_path, copy_to_path, recurse=True)
            self.assertTrue(os.path.isdir(copy_to_path))
            for _file in files:
                _filepath = os.path.sep.join([to_copy_dir_path, _file])
                with open(_filepath, 'w') as fh:
                    fh.writelines(['asdf'])
            self.client.scp_recv(to_copy_dir_path, copy_to_path, recurse=True)
            for _file in files:
                local_file_path = os.path.sep.join([copy_to_path, _file])
                self.assertTrue(os.path.isfile(local_file_path))
        finally:
            for _path in (to_copy_dir_path, copy_to_path):
                try:
                    shutil.rmtree(_path)
                except Exception:
                    pass

    def test_copy_file_abspath_recurse(self):
        cur_dir = os.path.dirname(__file__)
        dir_name_to_copy = 'a_dir'
        files = ['file1', 'file2']
        dir_paths = [cur_dir, dir_name_to_copy]
        to_copy_dir_path = os.path.abspath(os.path.sep.join(dir_paths))
        copy_to_path = '/tmp/dest_path//'
        for _path in (copy_to_path, to_copy_dir_path):
            try:
                shutil.rmtree(_path)
            except Exception:
                pass
        try:
            try:
                os.makedirs(to_copy_dir_path)
            except OSError:
                pass
            self.client.copy_file(to_copy_dir_path, copy_to_path, recurse=True)
            self.assertTrue(os.path.isdir(copy_to_path))
            for _file in files:
                _filepath = os.path.sep.join([to_copy_dir_path, _file])
                with open(_filepath, 'w') as fh:
                    fh.writelines(['asdf'])
            self.client.copy_file(to_copy_dir_path, copy_to_path, recurse=True)
            self.assertFalse(os.path.exists(os.path.expanduser('~/tmp')))
            for _file in files:
                local_file_path = os.path.sep.join([copy_to_path, _file])
                self.assertTrue(os.path.isfile(local_file_path))
        finally:
            for _path in (copy_to_path, to_copy_dir_path):
                try:
                    shutil.rmtree(_path)
                except Exception:
                    pass

    def test_copy_file_remote_dir_relpath(self):
        cur_dir = os.path.dirname(__file__)
        dir_base_dir = 'a_dir'
        dir_name_to_copy = '//'.join([dir_base_dir, 'dir1', 'dir2'])
        file_to_copy = 'file_to_copy'
        dir_path = [cur_dir, file_to_copy]
        copy_from_file_path = os.path.abspath(os.path.sep.join(dir_path))
        copy_to_file_path = '///'.join([dir_name_to_copy, file_to_copy])
        copy_to_abs_path = os.path.abspath(os.path.expanduser('~/' + copy_to_file_path))
        copy_to_abs_dir = os.path.abspath(os.path.expanduser('~/' + dir_base_dir))
        try:
            os.unlink(copy_from_file_path)
        except Exception:
            pass
        try:
            shutil.rmtree(copy_to_abs_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            with open(copy_from_file_path, 'w') as fh:
                fh.writelines(['asdf'])
            self.client.copy_file(copy_from_file_path, copy_to_file_path)
            self.assertTrue(os.path.isfile(copy_to_abs_path))
        finally:
            try:
                os.unlink(copy_from_file_path)
            except Exception:
                pass
            try:
                shutil.rmtree(copy_to_abs_dir, ignore_errors=True)
            except Exception:
                pass

    def test_sftp_mkdir_abspath(self):
        remote_dir = '/tmp/dir_to_create/dir1/dir2/dir3'
        _sftp = self.client._make_sftp()
        try:
            self.client.mkdir(_sftp, remote_dir)
            self.assertTrue(os.path.isdir(remote_dir))
            self.assertFalse(os.path.exists(os.path.expanduser('~/tmp')))
        finally:
            for _dir in (remote_dir, os.path.expanduser('~/tmp')):
                try:
                    shutil.rmtree(_dir)
                except Exception:
                    pass

    def test_sftp_mkdir_rel_path(self):
        remote_dir = 'dir_to_create/dir1/dir2/dir3'
        try:
            shutil.rmtree(os.path.expanduser('~/' + remote_dir))
        except Exception:
            pass
        _sftp = self.client._make_sftp()
        try:
            self.client.mkdir(_sftp, remote_dir)
            self.assertTrue(os.path.exists(os.path.expanduser('~/' + remote_dir)))
        finally:
            for _dir in (remote_dir, os.path.expanduser('~/tmp')):
                try:
                    shutil.rmtree(_dir)
                except Exception:
                    pass

    def test_scp_recv_large_file(self):
        cur_dir = os.path.dirname(__file__)
        file_name = 'file1'
        file_copy_to = 'file_copied'
        file_path_from = os.path.sep.join([cur_dir, file_name])
        file_copy_to_dirpath = os.path.expanduser('~/') + file_copy_to
        for _path in (file_path_from, file_copy_to_dirpath):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                # ~300MB
                for _ in range(20000000):
                    fh.write(b"adsfasldkfjabafj")
            self.client.scp_recv(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_dirpath))
            read_file_size = os.stat(file_path_from).st_size
            written_file_size = os.stat(file_copy_to_dirpath).st_size
            self.assertEqual(read_file_size, written_file_size)
            sha = sha256()
            with open(file_path_from, 'rb') as fh:
                for block in fh:
                    sha.update(block)
            read_file_hash = sha.hexdigest()
            sha = sha256()
            with open(file_copy_to_dirpath, 'rb') as fh:
                for block in fh:
                    sha.update(block)
            written_file_hash = sha.hexdigest()
            self.assertEqual(read_file_hash, written_file_hash)
        finally:
            for _path in (file_path_from, file_copy_to_dirpath):
                try:
                    os.unlink(_path)
                except Exception:
                    pass

    def test_scp_send_large_file(self):
        cur_dir = os.path.dirname(__file__)
        file_name = 'file1'
        file_copy_to = 'file_copied'
        file_path_from = os.path.sep.join([cur_dir, file_name])
        file_copy_to_dirpath = os.path.expanduser('~/') + file_copy_to
        for _path in (file_path_from, file_copy_to_dirpath):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                # ~300MB
                for _ in range(20000000):
                    fh.write(b"adsfasldkfjabafj")
            self.client.scp_send(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_dirpath))
            # OS file flush race condition
            sleep(.1)
            read_file_size = os.stat(file_path_from).st_size
            written_file_size = os.stat(file_copy_to_dirpath).st_size
            self.assertEqual(read_file_size, written_file_size)
            sha = sha256()
            with open(file_path_from, 'rb') as fh:
                for block in fh:
                    sha.update(block)
            read_file_hash = sha.hexdigest()
            sha = sha256()
            with open(file_copy_to_dirpath, 'rb') as fh:
                for block in fh:
                    sha.update(block)
            written_file_hash = sha.hexdigest()
            self.assertEqual(read_file_hash, written_file_hash)
        finally:
            for _path in (file_path_from, file_copy_to_dirpath):
                try:
                    os.unlink(_path)
                except Exception:
                    pass

    def test_scp_send_dir_target(self):
        cur_dir = os.path.dirname(__file__)
        file_name = 'file1'
        file_path_from = os.path.sep.join([cur_dir, file_name])
        file_copy_to_dirpath = os.path.expanduser('~/')
        file_copy_to_abs = file_copy_to_dirpath + file_name
        for _path in (file_path_from, file_copy_to_abs):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                fh.write(b"adsfasldkfjabafj")
            self.client.scp_send(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_abs))
        finally:
            for _path in (file_path_from, file_copy_to_abs):
                try:
                    os.unlink(_path)
                except OSError:
                    pass
        # Relative path
        file_copy_to_dirpath = './'
        for _path in (file_path_from, file_copy_to_abs):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                fh.write(b"adsfasldkfjabafj")
            self.client.scp_send(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_abs))
        finally:
            for _path in (file_path_from, file_copy_to_abs):
                try:
                    os.unlink(_path)
                except OSError:
                    pass

    def test_scp_recv_dir_target(self):
        cur_dir = os.path.dirname(__file__)
        file_name = 'file1'
        file_path_from = os.path.sep.join([cur_dir, file_name])
        file_copy_to_dirpath = os.path.expanduser('~/')
        file_copy_to_abs = file_copy_to_dirpath + file_name
        for _path in (file_path_from, file_copy_to_abs):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                fh.write(b"adsfasldkfjabafj")
            self.client.scp_recv(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_abs))
        finally:
            for _path in (file_path_from, file_copy_to_abs):
                try:
                    os.unlink(_path)
                except OSError:
                    pass
        # Relative path
        file_copy_to_dirpath = './'
        for _path in (file_path_from, file_copy_to_abs):
            try:
                os.unlink(_path)
            except OSError:
                pass
        try:
            with open(file_path_from, 'wb') as fh:
                fh.write(b"adsfasldkfjabafj")
            self.client.scp_send(file_path_from, file_copy_to_dirpath)
            self.assertTrue(os.path.isfile(file_copy_to_abs))
        finally:
            for _path in (file_path_from, file_copy_to_abs):
                try:
                    os.unlink(_path)
                except OSError:
                    pass

    def test_interactive_shell(self):
        with self.client.open_shell() as shell:
            shell.run(self.cmd)
            shell.run(self.cmd)
        stdout = list(shell.stdout)
        self.assertListEqual(stdout, [self.resp, self.resp])
        self.assertEqual(shell.exit_code, 0)

    def test_interactive_shell_exit_code(self):
        with self.client.open_shell() as shell:
            shell.run(self.cmd)
            shell.run('sleep 1')
            shell.run(self.cmd)
            shell.run('exit 1')
        stdout = list(shell.stdout)
        self.assertListEqual(stdout, [self.resp, self.resp])
        self.assertEqual(shell.exit_code, 1)


    # TODO
    # * scp send recursive
    # * scp recv recursive local dir permission denied
    # * scp_recv remote file not exists exception
    # * scp send open local file exception
    # * read output callback
    # * identity auth success
    # * connect init retries
    # * handshake retries
    # * agent forwarding
    # * password auth
    # * disconnect exception
    # * SFTP init exception
    # * sftp openfh exception
    # * sftp get exception
    # * copy file local_file dir no recurse exception
