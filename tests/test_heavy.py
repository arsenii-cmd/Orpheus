"""The hub and the laptop (heavy.py): the laptop connects by itself, over TLS with the hub's certificate pinned
and a token, and does the hearing, the speaking and the thinking the hub asks for."""

import asyncio
import datetime
import ssl
import threading

import numpy as np
import pytest

pytest.importorskip("websockets")
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402

from orpheus.config import Config  # noqa: E402
from orpheus.heavy import RemoteHeavy, Unreachable, work  # noqa: E402


def certificate(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orpheus-hub")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    return folder / "cert.pem", folder / "key.pem"


class FakeHeavy:
    def __init__(self):
        self.heard = []

    async def hear(self, samples, headset=None, strict=False):
        self.heard.append((len(samples), headset, strict))
        return "который час", 0.8, True, False

    async def synth(self, text, voice=""):
        return np.linspace(-0.5, 0.5, 48000, dtype=np.float32), 22050

    def status(self):
        return {"count": 3, "needed": 10, "mode": "log"}

    async def enroll(self, samples, headset=None):
        return None

    async def enroll_reset(self):
        pass


class FakeLLM:
    def chat(self, messages, tools=None, num_predict=None):
        for word in ["Пятнадцать ", "минут ", "третьего."]:
            yield {"message": {"content": word}}
        yield {"done": True}


def test_the_laptop_hears_speaks_and_thinks_for_the_hub(tmp_path):
    cert, key = certificate(tmp_path)
    remote = RemoteHeavy(log=lambda *_: None)
    laptop = FakeHeavy()

    async def scenario():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)

        def check(connection, request):
            return None if request.headers.get("Authorization") == "Bearer secret" else \
                connection.respond(401, "unauthorized\n")

        async with serve(remote.serve, "127.0.0.1", 0, ssl=context, process_request=check, max_size=None) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(Unreachable):
                await remote.hear(np.zeros(16000, dtype=np.float32))  # no laptop yet
            worker = asyncio.create_task(work(Config(), "wss://127.0.0.1:%d/worker" % port, "secret", cert,
                                              log=lambda *_: None, heavy=laptop, llm=FakeLLM()))
            await asyncio.wait_for(remote.connected.wait(), 10)
            await asyncio.sleep(0.1)
            assert remote.status() == {"count": 3, "needed": 10, "mode": "log"}
            heard = await remote.hear(np.zeros(32000, dtype=np.float32), headset=True, strict=True)
            assert heard == ("который час", 0.8, True, False)
            assert laptop.heard == [(32000, True, True)]  # all of the audio came before the job
            samples, rate = await remote.synth("Пятнадцать минут третьего.", "male")
            assert rate == 22050 and len(samples) == 48000 and abs(samples[0] + 0.5) < 1e-3
            said = await asyncio.to_thread(lambda: [c for c in remote.llm.chat([{"role": "user", "content": "?"}])])
            assert "".join(c.get("message", {}).get("content", "") for c in said) == "Пятнадцать минут третьего."
            worker.cancel()

    asyncio.run(scenario())


def test_a_wrong_token_is_not_let_in(tmp_path):
    cert, key = certificate(tmp_path)
    remote = RemoteHeavy(log=lambda *_: None)
    logged = []

    async def scenario():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)

        def check(connection, request):
            return None if request.headers.get("Authorization") == "Bearer secret" else \
                connection.respond(401, "unauthorized\n")

        async with serve(remote.serve, "127.0.0.1", 0, ssl=context, process_request=check) as server:
            port = server.sockets[0].getsockname()[1]
            worker = asyncio.create_task(work(Config(), "wss://127.0.0.1:%d/worker" % port, "wrong", cert,
                                              log=logged.append, heavy=FakeHeavy(), llm=FakeLLM()))
            await asyncio.sleep(1)
            assert not remote.connected.is_set() and any("нет связи" in m for m in logged)
            worker.cancel()

    asyncio.run(scenario())
