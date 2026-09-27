package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"github.com/syndtr/goleveldb/leveldb/util"
	"time"
)

type receipt struct {
	Tx            string `json:"tx_hash"`
	From          string `json:"from"`
	To            string `json:"to"`
	Source        uint64 `json:"source_shard"`
	Destination   uint64 `json:"destination_shard"`
	Amount        string `json:"amount_atto"`
	Block         uint64 `json:"source_block"`
	Hash          string `json:"source_hash"`
	ReceivedBlock uint64 `json:"received_block,omitempty"`
}

func parseReceipt(x item, source, destination, n uint64, hash []byte) receipt {
	f := fields(x, 6)
	require(len(scalar(f[0])) == 32 && len(scalar(f[1])) == 20 && len(scalar(f[2])) == 20, "malformed cross-shard receipt")
	require(source < 4 && destination < 4 && source != destination, "invalid cross-shard route")
	require(number(f[3]) == source && number(f[4]) == destination, "receipt shard mismatch")
	return receipt{Tx: hx(scalar(f[0])), From: hx(scalar(f[1])), To: hx(scalar(f[2])), Source: source, Destination: destination, Amount: integer(f[5]).String(), Block: n, Hash: hx(hash)}
}
func outgoingAt(db kvReader, r *report, n uint64, hash []byte, h header) {
	if bytes.Equal(h.out, emptyTrie) {
		return
	}
	var combined []byte
	for dest := uint64(0); dest < 4; dest++ {
		key := append([]byte("cxReceipt"), make([]byte, 12)...)
		binary.BigEndian.PutUint32(key[9:13], uint32(dest))
		binary.BigEndian.PutUint64(key[13:21], n)
		key = append(key, hash...)
		raw, ok := db.get(key)
		if !ok {
			continue
		}
		xs := decode(raw)
		require(xs.list, "invalid outgoing receipt list")
		if len(xs.children) == 0 {
			continue
		}
		id := make([]byte, 4)
		binary.BigEndian.PutUint32(id, uint32(dest))
		combined = append(combined, id...)
		combined = append(combined, listRoot(xs.children)...)
		for _, x := range xs.children {
			v := parseReceipt(x, uint64(r.Shard), dest, n, hash)
			r.Counts["outgoing_receipts_verified"]++
			if _, selected := r.Accounts[v.To]; selected || r.AllAccounts {
				r.Outgoing = append(r.Outgoing, v)
			}
		}
	}
	require(len(combined) > 0 && bytes.Equal(keccak(combined), h.out), "outgoing root mismatch or missing receipt groups")
}
func incomingAt(db kvReader, r *report, n uint64, hash []byte, h header) {
	if bytes.Equal(h.in, emptyTrie) {
		return
	}
	raw, ok := db.get(numbered('b', n, hash))
	require(ok, "missing body with incoming receipts")
	tag, f := tagged(decode(raw))
	idx := 2
	if tag == "v2" {
		idx = 3
	} else {
		require(tag == "v1", "unsupported incoming body tag")
	}
	require(len(f) == idx+1 && f[idx].list, "invalid incoming body shape")
	proofs := f[idx].children
	// Never repair historical bitmap defects using unverified hints.
	if !bytes.Equal(listRoot(proofs), h.in) {
		fail("incoming root mismatch at shard %d block %d; historical body repair/evidence required", r.Shard, n)
	}
	for _, proof := range proofs {
		p := fields(proof, 5)
		require(p[0].list, "invalid proof receipts")
		m := fields(p[1], 6)
		srcNum := number(m[0])
		srcHash := scalar(m[1])
		srcShard := number(m[2])
		require(len(srcHash) == 32, "invalid source hash")
		sh := parseHeader(p[2].raw)
		require(bytes.Equal(keccak(p[2].raw), srcHash) && sh.number == srcNum && sh.shard == srcShard, "incoming source identity mismatch")
		require(bytes.Equal(sh.out, scalar(m[3])), "incoming source root mismatch")
		require(m[4].list && m[5].list && len(m[4].children) == len(m[5].children), "invalid receipt merkle proof")
		var combined []byte
		found := false
		prev := -1
		for i, s := range m[4].children {
			dest := number(s)
			require(dest < 4 && int(dest) > prev, "invalid destination order")
			prev = int(dest)
			root := scalar(m[5].children[i])
			require(len(root) == 32, "invalid shard receipt root")
			id := make([]byte, 4)
			binary.BigEndian.PutUint32(id, uint32(dest))
			combined = append(combined, id...)
			combined = append(combined, root...)
			if dest == uint64(r.Shard) {
				require(bytes.Equal(listRoot(p[0].children), root), "incoming receipt group root mismatch")
				found = true
			}
		}
		require(found && bytes.Equal(keccak(combined), sh.out), "invalid incoming receipt proof")
		for _, x := range p[0].children {
			v := parseReceipt(x, srcShard, uint64(r.Shard), srcNum, srcHash)
			v.ReceivedBlock = n
			r.Counts["incoming_receipts_verified"]++
			if _, selected := r.Accounts[v.To]; selected || r.AllAccounts {
				r.Incoming = append(r.Incoming, v)
			}
		}
	}
}
func scanReceipts(db *database, r *report) {
	// Canonical heights, not the cx lookup index: missing/stale lookups must not
	// masquerade as pending funds. Require every height and a linked header chain.
	it := db.NewIterator(util.BytesPrefix([]byte{'h'}), nil)
	defer it.Release()
	expected := uint64(0)
	var previous []byte
	last := time.Now()
	for it.Next() {
		check(db.ctx.Err())
		key := it.Key()
		if len(key) != 10 || key[9] != 'n' {
			continue
		}
		n := binary.BigEndian.Uint64(key[1:9])
		if n > r.Cutoff.Block {
			break
		}
		require(n == expected, "gap in canonical history; complete archive required")
		hash := append([]byte{}, it.Value()...)
		require(len(hash) == 32, "bad canonical hash")
		h := readHeader(db, n, hash)
		require(h.shard == uint64(r.Shard), "header belongs to wrong shard")
		if n > 0 {
			require(bytes.Equal(h.parent, previous), "canonical parent chain mismatch")
		}
		outgoingAt(db, r, n, hash, h)
		incomingAt(db, r, n, hash, h)
		previous = hash
		expected++
		r.Counts["canonical_headers_verified"] = expected
		if time.Since(last) > 10*time.Second {
			fmt.Printf("receipt history shard=%d height=%d/%d\n", r.Shard, n, r.Cutoff.Block)
			last = time.Now()
		}
	}
	check(it.Error())
	require(expected == r.Cutoff.Block+1, "canonical history ends before cutoff")
	require(hx(previous) == r.Cutoff.Hash, "history does not reach pinned cutoff")
}
