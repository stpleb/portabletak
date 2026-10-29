from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.openmanet import provision


class Mesh:
    """Fake `ip`/`ping`: which addresses this node has and which other hosts answer."""
    def __init__(self, local=(), taken=()):
        self.local, self.taken = set(local), set(taken)

    def __call__(self, cmd, **kw):
        out = ''
        if cmd[:3] == ['ip', '-4', '-o']:
            out = ''.join('5: br-ahwlan    inet %s/32 scope global br-ahwlan\n' % a for a in self.local)
        elif cmd[:3] == ['ip', 'neigh', 'show'] and cmd[3] in self.taken:
            out = '%s dev br-ahwlan lladdr 02:00:00:00:00:01 REACHABLE\n' % cmd[3]
        return type('R', (), {'stdout': out, 'returncode': 0})()


class ServiceAddressTests(unittest.TestCase):
    def choose(self, current, mesh):
        return provision.choose_service_address(current, 'br-ahwlan', run=mesh)

    def test_keeps_its_own_when_free_or_here(self):
        self.assertEqual(self.choose('10.41.250.10', Mesh()), '10.41.250.10')
        self.assertEqual(self.choose('10.41.250.10', Mesh(local=['10.41.250.10'])), '10.41.250.10')
        self.assertEqual(self.choose('10.41.250.11', Mesh()), '10.41.250.11')   # stays where it moved

    def test_second_server_takes_the_next_free(self):
        mesh = Mesh(taken=['10.41.250.10'])
        self.assertEqual(self.choose('10.41.250.10', mesh), '10.41.250.11')
        mesh = Mesh(taken=['10.41.250.10', '10.41.250.11'])
        self.assertEqual(self.choose('10.41.250.10', mesh), '10.41.250.12')

    def test_all_taken_keeps_its_own(self):
        mesh = Mesh(taken=provision.SERVICE_POOL)
        self.assertEqual(self.choose('10.41.250.10', mesh), '10.41.250.10')

    def test_cannot_check_keeps_its_own(self):
        def broken(cmd, **kw):
            raise OSError('no ip')
        self.assertEqual(self.choose('10.41.250.10', broken), '10.41.250.10')


if __name__ == '__main__':
    unittest.main()
