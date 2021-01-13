# Copyright (c) 2019, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import pytest

from collections import OrderedDict

from dask.distributed import Client
from dask.distributed import wait

try:
    from raft.dask import Comms
    from raft.dask.common import nccl
    from raft.dask.common import local_handle
    from raft.dask.common import perform_test_comms_send_recv
    from raft.dask.common import perform_test_comms_allreduce
    from raft.dask.common import perform_test_comms_bcast
    from raft.dask.common import perform_test_comms_reduce
    from raft.dask.common import perform_test_comms_allgather
    from raft.dask.common import perform_test_comms_reducescatter
    from raft.dask.common import perform_test_comm_split
    pytestmark = pytest.mark.mg
except ImportError:
    pytestmark = pytest.mark.skip


def test_comms_init_no_p2p(cluster):

    client = Client(cluster)

    try:
        cb = Comms(verbose=True)
        cb.init()

        assert cb.nccl_initialized is True
        assert cb.ucx_initialized is False

    finally:

        cb.destroy()
        client.close()


def func_test_collective(func, sessionId, root):
    handle = local_handle(sessionId)
    return func(handle, root)


def func_test_send_recv(sessionId, n_trials):
    handle = local_handle(sessionId)
    return perform_test_comms_send_recv(handle, n_trials)


def func_test_comm_split(sessionId, n_trials):
    handle = local_handle(sessionId)
    return perform_test_comm_split(handle, n_trials)

def func_chk_uid_on_scheduler(sessionId, uniqueId, dask_scheduler):
    if (not hasattr(dask_scheduler, '_raft_comm_state')):
        return 1

    state_object = dask_scheduler._raft_comm_state
    if (sessionId not in state_object):
        return 2

    session_state = state_object[sessionId]
    if ('nccl_uid' not in dask_scheduler._raft_comm_state[sessionId]):
        return 3

    nccl_uid = session_state['nccl_uid']
    if (nccl_uid != uniqueId):
        return 4

    return 0

def func_chk_uid_on_worker(sessionId, uniqueId):
    from dask.distributed import get_worker

    worker_state = get_worker()
    if (not hasattr(worker_state, '_raft_comm_state')):
        return 1

    state_object = worker_state._raft_comm_state
    if (sessionId not in state_object):
        return 2

    session_state = state_object[sessionId]
    if ('nccl_uid' not in session_state):
        return 3

    nccl_uid = session_state['nccl_uid']
    if (nccl_uid != uniqueId):
        return 4

    return 0


def test_handles(cluster):

    client = Client(cluster)

    def _has_handle(sessionId):
        return local_handle(sessionId) is not None

    try:
        cb = Comms(verbose=True)
        cb.init()

        dfs = [client.submit(_has_handle,
                             cb.sessionId,
                             pure=False,
                             workers=[w])
               for w in cb.worker_addresses]
        wait(dfs, timeout=5)

        assert all(client.compute(dfs, sync=True))

    finally:
        cb.destroy()
        client.close()


if pytestmark.markname != 'skip':
    functions = [perform_test_comms_allgather,
                 perform_test_comms_allreduce,
                 perform_test_comms_bcast,
                 perform_test_comms_reduce,
                 perform_test_comms_reducescatter]
else:
    functions = [None]


@pytest.mark.parametrize("root_location", ['client', 'worker', 'scheduler'])
def test_nccl_root_placement(client, root_location):

    cb = None
    try:
        cb = Comms(verbose=True, client=client, nccl_root_location=root_location)
        cb.init()

        worker_addresses = list(OrderedDict.fromkeys(
            client.scheduler_info()["workers"].keys()))

        if (root_location in ('worker',)):
            result = client.run(func_chk_uid_on_worker,
                                cb.sessionId,
                                cb.uniqueId,
                                workers=[worker_addresses[0]])[worker_addresses[0]]
        elif (root_location in ('scheduler',)):
            result = client.run_on_scheduler(func_chk_uid_on_scheduler, cb.sessionId, cb.uniqueId)
        else:
            result = int(cb.uniqueId == None)

        assert (result == 0)

    finally:
        if (cb):
            cb.destroy()

@pytest.mark.parametrize("func", functions)
@pytest.mark.parametrize("root_location", ['client', 'worker', 'scheduler'])
@pytest.mark.nccl
def test_collectives(client, func, root_location):

    try:
        cb = Comms(verbose=True, client=client, nccl_root_location=root_location)
        cb.init()

        for k, v in cb.worker_info(cb.worker_addresses).items():

            dfs = [client.submit(func_test_collective,
                                 func,
                                 cb.sessionId,
                                 v["rank"],
                                 pure=False,
                                 workers=[w])
                   for w in cb.worker_addresses]
            wait(dfs, timeout=5)

            assert all([x.result() for x in dfs])
    finally:
        if (cb):
            cb.destroy()

@pytest.mark.nccl
def test_comm_split(client):

    cb = Comms(comms_p2p=True, verbose=True)
    cb.init()

    dfs = [client.submit(func_test_comm_split,
                         cb.sessionId,
                         3,
                         pure=False,
                         workers=[w])
           for w in cb.worker_addresses]

    wait(dfs, timeout=5)

    assert all([x.result() for x in dfs])


@pytest.mark.ucx
@pytest.mark.parametrize("n_trials", [1, 5])
def test_send_recv(n_trials, client):

    cb = Comms(comms_p2p=True, verbose=True)
    cb.init()

    dfs = [client.submit(func_test_send_recv,
                         cb.sessionId,
                         n_trials,
                         pure=False,
                         workers=[w])
           for w in cb.worker_addresses]

    wait(dfs, timeout=5)

    assert(list(map(lambda x: x.result(), dfs)))
