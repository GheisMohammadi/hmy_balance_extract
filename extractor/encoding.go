package main

// This package deliberately does not import Harmony, geth, or migration code.
// RLP and Merkle Patricia traversal below are independently implemented.
import (
	"bytes"
	"encoding/hex"
	"fmt"
	"golang.org/x/crypto/sha3"
	"math/big"
)

type auditError string

func fail(format string, args ...any) { panic(auditError(fmt.Sprintf(format, args...))) }
func check(err error) {
	if err != nil {
		fail("%v", err)
	}
}
func require(ok bool, msg string) {
	if !ok {
		fail("%s", msg)
	}
}
func keccak(b []byte) []byte { h := sha3.NewLegacyKeccak256(); h.Write(b); return h.Sum(nil) }
func hx(b []byte) string     { return "0x" + hex.EncodeToString(b) }
func unhex(s string, n int) []byte {
	require(len(s) == 2+n*2 && s[:2] == "0x", "invalid hex length/prefix")
	b, e := hex.DecodeString(s[2:])
	check(e)
	return b
}

type item struct {
	raw, data []byte
	children  []item
	list      bool
}

func decode(b []byte) item {
	x, n := parseRLP(b, 0)
	require(n == len(b), "trailing RLP bytes")
	return x
}
func parseRLP(b []byte, depth int) (item, int) {
	require(depth < 128 && len(b) > 0, "invalid/deep RLP")
	prefix := int(b[0])
	offset, length := 1, 0
	list := prefix >= 192
	switch {
	case prefix < 128:
		return item{raw: b[:1], data: b[:1]}, 1
	case prefix <= 183:
		length = prefix - 128
	case prefix <= 191:
		offset = 1 + prefix - 183
	case prefix <= 247:
		length = prefix - 192
	default:
		offset = 1 + prefix - 247
	}
	if (prefix > 183 && prefix < 192) || prefix > 247 {
		require(offset <= len(b) && b[1] != 0, "invalid RLP length")
		size := new(big.Int).SetBytes(b[1:offset])
		require(size.IsInt64(), "RLP length overflow")
		require(size.Int64() <= int64(len(b)-offset), "short RLP")
		length = int(size.Int64())
		require(length >= 56, "noncanonical long RLP")
	}
	require(offset <= len(b) && length <= len(b)-offset, "short RLP")
	x := item{raw: b[:offset+length], data: b[offset : offset+length], list: list}
	if list {
		rest := x.data
		for len(rest) > 0 {
			c, n := parseRLP(rest, depth+1)
			x.children = append(x.children, c)
			rest = rest[n:]
		}
	} else {
		require(!(length == 1 && x.data[0] < 128), "noncanonical RLP byte")
	}
	return x, offset + length
}
func fields(x item, n int) []item {
	require(x.list && len(x.children) == n, "unexpected RLP list shape")
	return x.children
}
func scalar(x item) []byte { require(!x.list, "expected RLP bytes"); return x.data }
func integer(x item) *big.Int {
	b := scalar(x)
	require(len(b) == 0 || b[0] != 0, "noncanonical integer")
	return new(big.Int).SetBytes(b)
}
func number(x item) uint64 {
	v := integer(x)
	require(v.IsUint64(), "integer overflow")
	return v.Uint64()
}
func rlpBytes(b []byte) []byte {
	if len(b) == 1 && b[0] < 128 {
		return append([]byte{}, b...)
	}
	return wrapRLP(b, 128, 183)
}
func wrapRLP(b []byte, short, long byte) []byte {
	if len(b) < 56 {
		return append([]byte{short + byte(len(b))}, b...)
	}
	n := new(big.Int).SetUint64(uint64(len(b))).Bytes()
	r := append([]byte{long + byte(len(n))}, n...)
	return append(r, b...)
}
func rlpList(xs ...[]byte) []byte { return wrapRLP(bytes.Join(xs, nil), 192, 247) }
func rlpNum(n uint64) []byte      { return rlpBytes(new(big.Int).SetUint64(n).Bytes()) }
func nibbles(b []byte) []byte {
	r := make([]byte, 0, 2*len(b))
	for _, v := range b {
		r = append(r, v>>4, v&15)
	}
	return r
}
func compact(path []byte, leaf bool) []byte {
	flag := byte(0)
	if leaf {
		flag = 2
	}
	out := []byte{}
	if len(path)%2 == 1 {
		out = append(out, (flag+1)<<4|path[0])
		path = path[1:]
	} else {
		out = append(out, flag<<4)
	}
	for i := 0; i < len(path); i += 2 {
		out = append(out, path[i]<<4|path[i+1])
	}
	return out
}
func expand(b []byte) ([]byte, bool) {
	require(len(b) > 0 && b[0]>>4 <= 3, "invalid compact path")
	ns := nibbles(b)
	leaf := ns[0]&2 != 0
	if ns[0]&1 == 1 {
		return ns[1:], leaf
	}
	require(ns[1] == 0, "invalid compact padding")
	return ns[2:], leaf
}

var emptyTrie = keccak([]byte{128})

type kvReader interface{ get([]byte) ([]byte, bool) }
type patricia struct {
	db   kvReader
	root []byte
}

func (t patricia) node(ref item) item {
	if ref.list {
		require(len(ref.raw) < 32, "oversized inline trie node")
		return ref
	}
	b := scalar(ref)
	require(len(b) == 32, "invalid trie hash reference")
	raw, ok := t.db.get(b)
	require(ok, "missing trie node (incomplete database)")
	require(bytes.Equal(keccak(raw), b), "trie node hash mismatch")
	return decode(raw)
}
func (t patricia) get(key []byte) ([]byte, bool) {
	if bytes.Equal(t.root, emptyTrie) {
		return nil, false
	}
	return t.lookup(item{data: t.root}, nibbles(key), 0)
}
func (t patricia) lookup(ref item, path []byte, depth int) ([]byte, bool) {
	require(depth <= 128, "trie depth exceeded")
	if !ref.list && len(ref.data) == 0 {
		return nil, false
	}
	node := t.node(ref)
	require(node.list, "trie node is not list")
	ns := node.children
	switch len(ns) {
	case 17:
		if len(path) == 0 {
			b := scalar(ns[16])
			return b, len(b) > 0
		}
		return t.lookup(ns[path[0]], path[1:], depth+1)
	case 2:
		prefix, leaf := expand(scalar(ns[0]))
		if !bytes.HasPrefix(path, prefix) {
			return nil, false
		}
		path = path[len(prefix):]
		if leaf {
			if len(path) != 0 {
				return nil, false
			}
			return scalar(ns[1]), true
		}
		require(len(prefix) > 0, "empty extension")
		return t.lookup(ns[1], path, depth+1)
	default:
		fail("invalid trie node arity %d", len(ns))
		return nil, false
	}
}
func (t patricia) walk(visit func([]byte, []byte)) {
	if !bytes.Equal(t.root, emptyTrie) {
		t.visit(item{data: t.root}, nil, visit, 0)
	}
}
func (t patricia) visit(ref item, path []byte, visit func([]byte, []byte), depth int) {
	require(depth <= 128 && len(path) <= 64, "invalid state trie depth")
	if !ref.list && len(ref.data) == 0 {
		return
	}
	node := t.node(ref)
	require(node.list, "trie node not list")
	emit := func(p, v []byte) {
		require(len(p) == 64, "invalid secure key")
		key := make([]byte, 32)
		for i := range key {
			key[i] = p[i*2]<<4 | p[i*2+1]
		}
		visit(key, v)
	}
	switch len(node.children) {
	case 17:
		for i := 0; i < 16; i++ {
			p := append(append([]byte{}, path...), byte(i))
			t.visit(node.children[i], p, visit, depth+1)
		}
		if len(scalar(node.children[16])) > 0 {
			emit(path, scalar(node.children[16]))
		}
	case 2:
		p, leaf := expand(scalar(node.children[0]))
		p = append(append([]byte{}, path...), p...)
		if leaf {
			emit(p, scalar(node.children[1]))
		} else {
			require(len(p) > len(path), "empty extension")
			t.visit(node.children[1], p, visit, depth+1)
		}
	default:
		fail("invalid trie node")
	}
}

// Independently build the small ordered receipt tries used in block headers.
type pair struct{ path, value []byte }

func childRef(encoded []byte) []byte {
	if len(encoded) < 32 {
		return encoded
	}
	return rlpBytes(keccak(encoded))
}
func buildTrie(rows []pair, depth int) []byte {
	require(len(rows) > 0, "empty trie build")
	if len(rows) == 1 {
		return rlpList(rlpBytes(compact(rows[0].path[depth:], true)), rlpBytes(rows[0].value))
	}
	common := 0
	for {
		idx := depth + common
		if idx >= len(rows[0].path) {
			break
		}
		same := true
		for _, r := range rows[1:] {
			if idx >= len(r.path) || r.path[idx] != rows[0].path[idx] {
				same = false
				break
			}
		}
		if !same {
			break
		}
		common++
	}
	if common > 0 {
		return rlpList(rlpBytes(compact(rows[0].path[depth:depth+common], false)), childRef(buildTrie(rows, depth+common)))
	}
	out := make([][]byte, 17)
	for i := range out {
		out[i] = rlpBytes(nil)
	}
	for i := 0; i < 16; i++ {
		var group []pair
		for _, r := range rows {
			if depth < len(r.path) && int(r.path[depth]) == i {
				group = append(group, r)
			}
		}
		if len(group) > 0 {
			out[i] = childRef(buildTrie(group, depth+1))
		}
	}
	for _, r := range rows {
		if depth == len(r.path) {
			out[16] = rlpBytes(r.value)
		}
	}
	return rlpList(out...)
}
func listRoot(xs []item) []byte {
	if len(xs) == 0 {
		return emptyTrie
	}
	rows := make([]pair, len(xs))
	for i, x := range xs {
		rows[i] = pair{nibbles(rlpNum(uint64(i))), x.raw}
	}
	return keccak(buildTrie(rows, 0))
}
