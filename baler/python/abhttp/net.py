# Copyright (c) 2016 Open Grid Computing, Inc. All rights reserved.
# Copyright (c) 2016 Sandia Corporation. All rights reserved.
#
# Under the terms of Contract DE-AC04-94AL85000, there is a non-exclusive
# license for use of this work by or on behalf of the U.S. Government.
# Export of this program may require a license from the United States
# Government.
#
# This software is available to you under a choice of one of two
# licenses.  You may choose to be licensed under the terms of the GNU
# General Public License (GPL) Version 2, available from the file
# COPYING in the main directory of this source tree, or the BSD-type
# license below:
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#      Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#      Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials provided
#      with the distribution.
#
#      Neither the name of Sandia nor the names of any contributors may
#      be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
#      Neither the name of Open Grid Computing nor the names of any
#      contributors may be used to endorse or promote products derived
#      from this software without specific prior written permission.
#
#      Modified source versions must be plainly marked as such, and
#      must not be misrepresented as being the original software.
#
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging
import httplib
import urllib
import json
import struct
import re
from datatype import *
from baler import bquery

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


# some constants
QUERY = "/query"

def align(num, interval):
    return int(num/interval)*interval

def list2csv(l):
    if l == None:
        return None
    if type(l) == str:
        return l
    try:
        return ",".join(l)
    except TypeError:
        return str(l)


def strnone(s):
    if s == None:
        return None
    return str(s)


class BHTTPResponseError(Exception):
    """Exception for HTTP Response Error.

    Attributes:
        status(int): the status of the HTTP response.
        reason(str): the reason from the server.
    """

    def __init__(self, resp):
        """Constructor.

        Args:
            resp(HTTPResponse): the HTTPResponse object.
        """
        self.status = resp.status
        self.reason = resp.reason

    def __str__(self):
        return "status %d: %s" % (self.status, self.reason)


class BStore2Conn(object):
    """Connection to local baler store (ver 2)."""

    def __init__(self, location, name=None):
        self.location = location
        self.name = name if name else location
        self.bstore = bquery.bq_open_store(location)
        if not self.bstore:
            raise Exception("Cannot open store: %s", location)
        self.imgq = None
        self._msgq = None
        self._msgn = 0
        self._msgi = 0
        self._msgd = "fwd"
        pass

    def __del__(self):
        if self.bstore:
            bquery.bq_store_close_free(self.bstore)
        pass

    def get_name(self):
        return self.name

    def _imgq_reset(self):
        if self.imgq:
            bquery.bimgquery_destroy(self.imgq)
            self.imgq = None

    def _msgq_reset(self):
        if self._msgq:
            bquery.bmsgquery_destroy(self._msgq)
            self._msgq = None

    def get_img(self, img_store, host_ids=None, ptn_ids=None,
                ts0=None, ts1=None):
        self._imgq_reset()
        self.imgq = bquery.bimgquery_create(
                    self.bstore,
                    list2csv(host_ids),
                    list2csv(ptn_ids),
                    strnone(ts0),
                    strnone(ts1),
                    str(img_store),
                    None
                )
        if not self.imgq:
            raise Exception("query failed")
        self.imgq_rc = bquery.biq_first_entry(self.imgq)
        pass

    def fetch_img(self, n=None):
        if not self.imgq:
            raise Exception("no imgq: get_img() error or not called")
        pxls = []
        i = 0
        # self.imgq set to first entry in self.get_img()
        while (self.imgq_rc == 0):
            if n and i>=n:
                break
            p = bquery.biq_entry_get_pixel(self.imgq)
            pxl = Pixel(p.sec, p.comp_id, p.ptn_id, p.count)
            pxls.append(pxl)
            i = i + 1
            self.imgq_rc = bquery.biq_next_entry(self.imgq)
        return pxls

    def get_img2(self, img_store, ts_begin, host_begin,
                spp, npp, width, height, ptn_ids=None):
        """ Prepare image2 data for to be fetched by `fetch_img2()`.

            NOTE0: if `ts_begin` is not aligned with `spp`, it will
                   automatically be aligned in this function.
            NOTE1: if `host_begin` is not aligned with `npp`, it will
                   automatically be aligned in this function.
        """
        if type(npp) != int:
            raise TypeError("npp must be an `int`")
        if type(spp) != int:
            raise TypeError("spp must be an `int`")

        # this creates 1-D array like `int pxls[height*width]` in C
        self._pxls = [0]*(width*height)
        self._idx = 0
        ts0 = align(ts_begin, spp)
        ts1 = ts0 + width*spp - 1
        h0 = align(host_begin, npp)
        h1 = h0 + height*npp - 1

        q = bquery.bimgquery_create(
                    self.bstore,
                    "%d-%d" % (h0, h1),
                    list2csv(ptn_ids),
                    strnone(ts0),
                    strnone(ts1),
                    img_store,
                    None
                )
        rc = bquery.biq_first_entry(q)
        while rc == 0:
            p = bquery.biq_entry_get_pixel(q)
            xidx = int((p.sec - ts0) / spp)
            yidx = int((p.comp_id - h0) / npp)
            try:
                self._pxls[yidx*width + xidx] += p.count
            except Exception as e:
                logger.warn("len: %d", len(self._pxls))
                logger.warn("yidx: %d", yidx)
                logger.warn("xidx: %d", xidx)
                logger.warn("idx: %d", yidx*width+xidx)
                logger.warn("ts0: %d", ts0)
                logger.warn("ts1: %d", ts1)
                logger.warn("sec: %d", p.sec)
                logger.warn("h0: %d", h0)
                logger.warn("h1: %d", h1)
                logger.warn("comp_id: %d", p.comp_id)
                logger.warn("spp: %d", spp)
                logger.warn("npp: %d", npp)
                logger.warn("width: %d", width)
                logger.warn("height: %d", height)
                raise
            rc = bquery.biq_next_entry(q)
        pass

    def fetch_img2(self, n=None):
        i = 0
        pxls = []
        while self._idx < len(self._pxls):
            if n and i >= n:
                break
            pxls.append(self._pxls[self._idx])
            self._idx += 1
            i += 1
        return pxls

    def get_ptn(self):
        ptns = bquery.getPatterns(self.bstore)
        self.ptns = {p[0]: Pattern.fromPyPattern(p) for p in ptns}
        pass

    def fetch_ptn(self):
        ptns = self.ptns
        self.ptns = None
        return ptns

    def get_msg(self, n=None, direction=None, session_id=None, host_ids=None,
                ptn_ids=None, ts0=None, ts1=None):
        # query object is session_id
        # need to simulate get/fetch behavior similar to over-the-network
        # operation.
        if session_id:
            self._msgq = session_id
            if direction == "bwd":
                firststep = bquery.bmq_prev_entry
            else:
                firststep = bquery.bmq_next_entry
        else:
            self._msgq = bquery.bmsgquery_create(
                                        self.bstore,
                                        list2csv(host_ids),
                                        list2csv(ptn_ids),
                                        strnone(ts0),
                                        strnone(ts1),
                                        0,
                                        None,
                                        None
                                    )
            if direction == "bwd":
                firststep = bquery.bmq_last_entry
            else:
                firststep = bquery.bmq_first_entry
        self._msgn = n
        self._msgi = 0
        self._msgd = direction
        if direction == "bwd":
            self._msgstep = bquery.bmq_prev_entry
        else:
            self._msgstep = bquery.bmq_next_entry
        self._msgrc = firststep(self._msgq)

    def fetch_msg(self, n=None):
        msgs = []
        i = 0
        while (self._msgrc == 0 and self._msgi < self._msgn):
            if n and i >= n:
                break
            m = bquery.bmq_get_PyMessage(self._msgq)
            msg = LogMessage.fromPyMsg(m)
            msgs.append(msg)
            i += 1
            self._msgi += 1
            self._msgrc = self._msgstep(self._msgq)
        return (self._msgq, msgs)

    def get_msg2(self, n=None, direction=None, pos=None,host_ids=None,
                 ptn_ids=None, ts0=None, ts1=None, curr=None):
        self._msgq_reset()
        self._msgq = bquery.bmsgquery_create(
                                    self.bstore,
                                    list2csv(host_ids),
                                    list2csv(ptn_ids),
                                    strnone(ts0),
                                    strnone(ts1),
                                    0,
                                    None,
                                    None
                                )
        assert(self._msgq != None)
        self._msgd = direction
        if self._msgd == "bwd":
            firststep = bquery.bmq_last_entry
        else:
            firststep = bquery.bmq_first_entry
        if pos:
            # recover position
            rc = bquery.bmq_set_pos(self._msgq, str(pos))
            if rc:
                raise Exception("bmq_set_pos() failed, rc: %d" % rc)
            if self._msgd == "bwd":
                firststep = bquery.bmq_prev_entry
            else:
                firststep = bquery.bmq_next_entry
        self._msgi = 0
        self._msgn = n
        if self._msgd == "bwd":
            self._msgstep = bquery.bmq_prev_entry
        else:
            self._msgstep = bquery.bmq_next_entry

        if pos and curr:
            self._msgrc = 0
        else:
            self._msgrc = firststep(self._msgq)

    def fetch_msg2(self, n=None):
        msgs = []
        i = 0
        while (self._msgrc == 0 and self._msgi < self._msgn):
            if n and i >= n:
                break
            m = bquery.bmq_get_PyMessage(self._msgq)
            msg = LogMessage.fromPyMsg(m)
            msgs.append(msg)
            i += 1
            self._msgi += 1
            self._msgrc = self._msgstep(self._msgq)
        return msgs

    def get_host(self):
        self.hosts = bquery.getHosts(self.bstore)

    def fetch_host(self):
        return self.hosts


class BHTTPDConn(object):
    """Connection to bhttpd.

    BHTTPDConn is an object hanlding a connection to a bhttpd location. It also
    has methods for communicating with the location. Use:

    ``get_XXX`` to make an HTTP GET request to the location for XXX query; and,
    ``fetch_XXX`` to fetch the result of XXX query.

    When a ``BHTTPDConn`` object experience an error, the caller shall should
    discard the object and should create and use a new ``BHTTPDConn``.
    """

    def __init__(self, location, name=None):
        """Constructor.

        Args:
            location (str): "host:port" describing bhttpd location.
        """
        logger.info("connecting to bhttpd: %s", location)
        self._server = location
        if not name:
            name = location
        self._name = name
        self._resp = None
        self._conn = httplib.HTTPConnection(location)
        self._conn.connect()

    def __del__(self):
        """Destructor."""
        logger.info("closing connection to bhttpd: %s", self._server)
        self._conn.close()

    def get_name(self):
        return self._name

    def _get(self, path, params = None):
        """Make an HTTP GET request to the server.

        NOTE: Please use fetch() method to obtain the response.
        """
        uri = path
        if params:
            _p = urllib.urlencode({
                        k: params[k] for k in params if params[k] != None
                 })
            uri = uri + "?" + _p
        logger.debug("requesting server: %s, uri: %s", self._server, uri)
        self._conn.request("GET", uri)
        self._resp = self._conn.getresponse()

    def _fetch(self, size=None):
        """Fetch results from the HTTP GET request.

        This method can be repeatedly called to obtain the result ``size`` bytes
        at a time. If there is no more data, ``None`` is returned.

        Args:
            size(int): the maximum size (bytes) to fetch.


        Returns:
            str: the data from the server response.
            None: if there is no more data.

        Raises:
            BHTTPResponseError: if the server response status is not 200.
        """
        logger.debug("fetching ...")
        if not self._resp:
            logger.debug("no response")
            return []
        if self._resp.status != 200:
            raise BHTTPResponseError(self._resp)
        data = self._resp.read(size)
        logger.debug("data length: %d", len(data))
        return data

    def get_img(self, img_store, host_ids=None, ptn_ids=None,
                ts0=None, ts1=None):
        """Request image query.

        The ``image`` query expects the server to return a list of Pixels
        (timestamp,comp_id,ptn_id,count) that match the query criteria. Think of
        it as a vector image.  ``get_img()`` results shall be obtained by
        ``fetch_img()`` method.

        Query ``host_ids``, ``ptn_ids``, ``ts0``, ``ts1`` are optional query
        constraints. Not specifying a constraint is the same as specifying it to
        match all records (e.g. ``ts0=0``).

        Args:
            img_store(str): the image store to query (e.g. "3600-1")
            host_ids(Optional[str or [str]]): optional string or list of strings
                describing ranges of host IDs.
            ptn_ids(Optional[str or [str]]): optional string or list of strings
                describing ranges of pattern IDs.
            ts0(Optional[int or str]): the begin time of the image region to
                query. It can be the number of seconds since epoch or the string
                in the form of "yyyy-mm-dd HH:MM:SS".
            ts1(Optional[int or str]): the end time of the image region to
                query. The format is the same as ts0.
        """
        if not img_store:
            raise Exception("img_store not specified")
        ptn_ids = list2csv(ptn_ids)
        host_ids = list2csv(host_ids)
        self._get(QUERY, {
            "type": "img",
            "img_store": img_store,
            "ts0": ts0,
            "ts1": ts1,
            "host_ids": host_ids,
            "ptn_ids": ptn_ids,
        })

    def fetch_img(self, n=None):
        """Fetch image pixels from the previous 'get_img()' request

        Args:
            n(int): the maximum number of pixels to fetch. None means no limit.

        Returns:
            [Pixel]: the array of pixels.
            []: empty array if there is no more data to fetch.
        """
        i = 0
        pxls = []
        while not n or i < n:
            data = self._fetch(4*4)
            if not data:
                break
            data = struct.unpack("!IIII", data)
            pxl = Pixel(data[0], data[1], data[2], data[3])
            pxls.append(pxl)
            i+=1
        return pxls

    def get_img2(self, img_store, ts_begin, host_begin,
                spp, npp, width, height, ptn_ids=None):
        """Request bhttpd for image2 data.

        Image2 data is different from image data. Instead of getting an array of
        descriptive pixels, the caller will get a consecutive array of counts
        describing the image in the requested area. Think of this as a raster
        (bitmap) image that the server will do the rendering and send the bitmap
        data back.

        The response data can be obtained by ``fetch_img2()`` method.

        Args:
            img_store(str): the image store to query.
            ts_begin(int or str): the begin timestamp of the image. It can be
                the number of seconds from the epoch or "yyyy-mm-dd HH:MM:SS".
            host_begin(int): the beginning of the host of the image to query.
            spp(int): seconds per pixel.
            npp(int): nodes per pixel.
            width(int): the image width (time axis), in number of pixels.
            height(int): the image height (node axis), in number of pixels.
            ptn_ids(Optional[str or [str]]): the string (or array of strings)
                describing ranges of patterns to compose the image. Leaving it
                as ``None`` means all available patterns.
        """
        # validate params
        for x in (img_store, ts_begin, host_begin,
                  spp, npp, width, height):
            if x == None:
                raise AttributeError("All parameters must be specified")
        ptn_ids = list2csv(ptn_ids)
        self._get(QUERY, {
            "type": "img2",
            "img_store": img_store,
            "ts_begin": ts_begin,
            "host_begin": host_begin,
            "spp": spp,
            "npp": npp,
            "width": width,
            "height": height,
            "ptn_ids": ptn_ids,
        })

    def fetch_img2(self, n=None):
        """Fetch image2  response.

        Args:
            n(int): the maximum number of pixels to obtain. ``None`` means no
                limit.

        Returns:
            [int]: an array of pattern occurrences in the image. The returned
                data is in the following order:
                [(x0,y0), (x1,y0), ..., (xWidth-1,y0),
                 (x0,y1), (x1,y1), ...
                 ...
                 (x0,yWidth-1),  ...    (xWidth-1,yWidth-1)]

                Where X is the time axis and Y is the node axis.
        """
        i = 0
        img = []
        while not n or i<n:
            x = self._fetch(4)
            if not x:
                break
            x = struct.unpack("!i", x)
            img.append(x)
            i+=1
        return img

    def get_ptn(self):
        """Request a pattern query."""
        self._ptn_data = None
        self._get(QUERY, {"type": "ptn", "use_ts_fmt": 1})

    def fetch_ptn(self):
        """Fetch pattern map fom the response.

        Returns:
            {int: Pattern}: a dictionary of ptn_id -> Pattern
        """
        _ptn_data = self._fetch()
        j = json.loads(_ptn_data)
        ret = {}

        for _p in j['result']:
            p = Pattern.fromJSONObj(_p)
            ret[p.ptn_id] = p

        return ret

    def get_msg(self, n=None, direction=None, session_id=None, host_ids=None,
                ptn_ids=None, ts0=None, ts1=None):
        self._msg_data = None
        ptn_ids = list2csv(ptn_ids)
        host_ids = list2csv(host_ids)
        self._get(QUERY, {
            "type": "msg",
            "n": n,
            "dir": direction,
            "session_id": session_id,
            "host_ids": host_ids,
            "ptn_ids": ptn_ids,
            "ts0": ts0,
            "ts1": ts1
        })

    def fetch_msg(self, n=None):
        """Fetch messages from the response.

        Args:
            n(int): the maximum number of messages to fetch.

        Returns:
            (int, [LogMessage]): a tuple of session_id and an array of
                LogMessage objects.

        Note:
            This interface has an argument ``n`` just so that it gets along with
            the other interface. The number of messages is not a lot, as it is
            already limited by the GET query parameter ``n``. So, it is
            advisable to fetch with ``n=None``.
        """
        if not self._msg_data:
            self._msg_data = self._fetch()
            j = json.loads(self._msg_data)
            self._session_id = j['session_id']
            self._msgs = [LogMessage.fromJSONObj(x) for x in j['msgs']]
            self._msg_idx = 0

        ret = []
        i = 0
        while not n or i < n:
            if self._msg_idx >= len(self._msgs):
                break
            ret.append(self._msgs[self._msg_idx])
            self._msg_idx += 1

        return (self._session_id, ret)

    def get_msg2(self, n=None, direction=None, pos=None,host_ids=None,
                 ptn_ids=None, ts0=None, ts1=None, curr=None):
        self._msg_data = None
        ptn_ids = list2csv(ptn_ids)
        host_ids = list2csv(host_ids)
        self._get(QUERY, {
            "type": "msg2",
            "n": n,
            "dir": direction,
            "pos": pos,
            "host_ids": host_ids,
            "ptn_ids": ptn_ids,
            "ts0": ts0,
            "ts1": ts1,
            "curr": curr
        })

    def fetch_msg2(self, n=None):
        """Fetch messages from the response.

        Args:
            n(int): the maximum number of messages to fetch.

        Returns:
            [LogMessage]: a list of LogMessage objects.

        Note:
            This interface has an argument ``n`` just so that it gets along with
            the other interface. The number of messages is not a lot, as it is
            already limited by the GET query parameter ``n``. So, it is
            advisable to fetch with ``n=None``.
        """
        if not self._msg_data:
            self._msg_data = self._fetch()
            j = json.loads(self._msg_data)
            self._msgs = [LogMessage.fromJSONObj(x) for x in j['msgs']]
            self._msg_idx = 0

        ret = []
        i = 0
        while not n or i < n:
            if self._msg_idx >= len(self._msgs):
                break
            ret.append(self._msgs[self._msg_idx])
            self._msg_idx += 1

        return ret


    def get_host(self):
        """Make a GET request for host information."""
        self._get(QUERY, {"type": "host"})

    def fetch_host(self):
        """Fetch the host map result.

        Returns:
            {int: str}: a dictionary of host_id -> host.
        """
        self._host_data = self._fetch()
        j = json.loads(self._host_data)
        h = j["host_ids"]
        return {int(k): str(h[k]) for k in h}


class BHTTPDConnMsgQueryIterPos(object):
    """Convenient object for iterator positioning."""

    def __init__(self, itr, str_pos=None):
        self.itr = itr
        itr = BHTTPDConnMsgQueryIter()
        _curr_msg = itr._curr_msg
        if _curr_msg and _curr_msg.pos:
            pass

FWD="fwd"
BWD="bwd"

class BHTTPDConnMsgQueryIter(object):
    """Mesage query iterator over BHTTPDConn."""

    BUFFERING=2
    BUFFERRED=3
    EOF=10

    BUFSLOTS=40

    def __init__(self, conn, host_ids=None, ptn_ids=None,
                 ts0=None, ts1=None):
        """Constructor.

        Args:
            conn(BHTTPDConn): BHTTPD connection handler.
            direction(str): "fwd" or "bwd".
            host_ids([csv]): host IDs (in ``conn`` namespace).
            ptn_ids([csv]): pattern IDs (in ``conn`` namespace).
            ts0(str or int): begin timestamp.
            ts1(str or int): end timestamp.
        """
        self._conn = conn
        self._direction = None
        self._host_ids = host_ids
        self._ptn_ids = ptn_ids
        self._ts0 = ts0
        self._ts1 = ts1
        self.reset()

    def reset(self):
        self._state = self.BUFFERRED
        self._curr_msg = None
        self._msg_buff = []
        self._curr_idx = None
        self._pos = None

    def _replenish_buff(self, curr=None):
        assert(self._state == self.BUFFERRED)
        logger.debug("replenishing, pos: '%s'", self._pos)
        self._state = self.BUFFERING
        self._conn.get_msg2(
            n = self.BUFSLOTS,
            pos = self._pos,
            direction=self._direction,
            host_ids=self._host_ids,
            ptn_ids=self._ptn_ids,
            ts0=self._ts0,
            ts1=self._ts1,
            curr=curr
        )
        msgs = self._conn.fetch_msg2()
        if not len(msgs):
            self.reset()
            self._state = self.EOF
            return
        if self._direction == BWD:
            # reverse list for BWD iteration
            msgs.reverse()
            self._curr_idx = len(msgs)
        else:
            self._curr_idx = -1
        self._msg_buff = msgs
        self._state = self.BUFFERRED

    def next(self):
        logger.debug("next: ENTERING, curr_idx: %s", self._curr_idx)
        self._direction = FWD
        if self._state == self.EOF:
            logger.debug("next: end")
            # reset state
            self._state = self.BUFFERRED
            return None
        assert(self._state == self.BUFFERRED)
        if self._curr_idx == None:
            # first call
            logger.debug("next: first_call")
            self._replenish_buff()
            return self.next()
        self._curr_idx += 1
        if len(self._msg_buff) <= self._curr_idx:
            # Need replenish
            self._replenish_buff()
            return self.next()
        self._curr_msg = self._msg_buff[self._curr_idx]
        self._pos = self._curr_msg.pos
        logger.debug("next: curr_idx: %s, returning '%s'",
                        self._curr_idx, str(self._curr_msg))
        return self._curr_msg

    def prev(self):
        logger.debug("prev: ENTERING, curr_idx: %s", self._curr_idx)
        self._direction = BWD
        if self._state == self.EOF:
            logger.debug("prev: end")
            # reset state
            self._state = self.BUFFERRED
            return None
        assert(self._state == self.BUFFERRED)
        if self._curr_idx == None:
            # first call
            logger.debug("prev: first_call")
            self._replenish_buff()
            return self.prev()
        self._curr_idx -= 1
        if self._curr_idx < 0:
            # Need replenish
            self._replenish_buff()
            return self.prev()
        self._curr_msg = self._msg_buff[self._curr_idx]
        self._pos = self._curr_msg.pos
        logger.debug("prev: curr_idx: %s, returning '%s'",
                        self._curr_idx, str(self._curr_msg))
        return self._curr_msg

    def get_curr_msg(self):
        return self._curr_msg

    def get_pos(self):
        """Get current position."""
        return self._pos

    def get_conn_name(self):
        """Returns connection name of the iterator."""
        return self._conn.get_name()

    def set_pos(self, pos_str, direction=None):
        """Set position, and return current message."""
        if not direction:
            direction = self._direction
        self.reset()
        logger.debug("pos_str: %s", pos_str)
        self._pos = pos_str
        self._direction = direction
        self._replenish_buff(curr=True)
        if direction == BWD:
            return self.prev()
        return self.next()

    def get_dir(self):
        """Returns last iterator direction."""
        return self._direction

    # These comparison is intended to be used in the min/max heap of iterators.
    # It works because host IDs in each iterator are mutually exclusive.

    def __eq__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        return self._curr_msg == other._curr_msg

    def __ne__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        return self._curr_msg != other._curr_msg

    def __lt__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        # invalid iterator is always greater than the valid iterator, so that
        # the invalid is always at the bottom of the heap.
        a = (self._curr_msg == None)
        b = (other._curr_msg == None)
        if a ^ b:
            if a:
                return False
            return True
        if self._direction == BWD:
            return self._curr_msg > other._curr_msg
        return self._curr_msg < other._curr_msg

    def __gt__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        # invalid iterator is always greater than the valid iterator, so that
        # the invalid is always at the bottom of the heap.
        a = (self._curr_msg == None)
        b = (other._curr_msg == None)
        if a ^ b:
            if a:
                return True
            return False
        if self._direction == BWD:
            return self._curr_msg < other._curr_msg
        return self._curr_msg > other._curr_msg

    def __le__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        # invalid iterator is always greater than the valid iterator, so that
        # the invalid is always at the bottom of the heap.
        a = (self._curr_msg == None)
        b = (other._curr_msg == None)
        if a ^ b:
            if a:
                return False
            return True
        if self._direction == BWD:
            return self._curr_msg >= other._curr_msg
        return self._curr_msg <= other._curr_msg

    def __ge__(self, other):
        if self._direction != other._direction:
            raise TypeError("comparing incompatible iterator")
        # invalid iterator is always greater than the valid iterator, so that
        # the invalid is always at the bottom of the heap.
        a = (self._curr_msg == None)
        b = (other._curr_msg == None)
        if a ^ b:
            if a:
                return False
            return True
        if self._direction == BWD:
            return self._curr_msg <= other._curr_msg
        return self._curr_msg >= other._curr_msg
