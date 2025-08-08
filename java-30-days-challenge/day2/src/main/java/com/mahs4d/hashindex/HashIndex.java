package com.mahs4d.hashindex;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

public final class HashIndex<K, V> {
    private static final class Node<K, V> {
        private final int hash;
        private final K key;
        private V value;
        private Node<K, V> next;

        Node(int hash, K key, V value, Node<K, V> next) {
            this.hash = hash;
            this.key = key;
            this.value = value;
            this.next = next;
        }

        int getHash() {
            return hash;
        }

        K getKey() {
            return key;
        }

        V getValue() {
            return value;
        }

        void setValue(V value) {
            this.value = value;
        }

        Node<K, V> getNext() {
            return next;
        }

        void setNext(Node<K, V> next) {
            this.next = next;
        }
    }

    private static final int DEFAULT_REQUESTED_CAPACITY=  16;
    private static final float DEFAULT_LOAD_FACTOR = 0.75f;

    private Node<K, V>[] buckets;
    private final float loadFactor;
    private int size;

    public HashIndex() {
        this(DEFAULT_REQUESTED_CAPACITY, DEFAULT_LOAD_FACTOR);
    }

    @SuppressWarnings("unchecked")
    public HashIndex(int requestedCapacity, float loadFactor) {
        int initialCapacity = calculateInitialCapacity(requestedCapacity);
        this.buckets = (Node<K, V>[]) new Node[initialCapacity];
        this.loadFactor = loadFactor;
    }

    private int calculateInitialCapacity(int requestedCapacity) {
        int n = 1;
        while (n < requestedCapacity) {
            n <<= 1;
        }
        return n;
    }

    public V put(K key, V value) {
        Node<K, V> node = getNode(key);
        if (node != null) {
            V oldValue = node.value;
            node.setValue(value);
            return oldValue;
        }

        int hash = hashKey(key);
        int bucketIndex = getHashBucketIndex(hash);

        buckets[bucketIndex] = new Node<>(hash, key, value, buckets[bucketIndex]);
        size++;
        if (size >= getThreshold()) {
            resize();
        }
        return null;
    }

    private Node<K, V> getNode(K key) {
        int hash = hashKey(key);
        int bucketIndex = getHashBucketIndex(hash);

        Node<K, V> node = buckets[bucketIndex];
        while (node != null) {
            if (node.hash == hash && Objects.equals(node.key, key)) {
                return node;
            }

            node = node.getNext();
        }

        return null;
    }

    private int hashKey(K key) {
        if (key == null) {
            return 0;
        }
        int h = key.hashCode();
        return h ^ (h >>> 16);
    }

    private int getHashBucketIndex(int hash) {
        return getHashBucketIndex(hash, getCapacity());
    }

    private int getHashBucketIndex(int hash, int capacity) {
        return hash & (capacity - 1);
    }

    @SuppressWarnings("unchecked")
    private void resize() {
        int newCapacity = getCapacity() * 2;
        Node<K, V>[] newBuckets = (Node<K, V>[]) new Node[newCapacity];

        for (Node<K, V> head : buckets) {
            Node<K, V> node = head;
            while (node != null) {
                Node<K, V> next = node.next;
                int bucketIndex = getHashBucketIndex(node.hash, newCapacity);
                node.setNext(newBuckets[bucketIndex]);
                newBuckets[bucketIndex] = node;
                node = next;
            }
        }

        this.buckets = newBuckets;
    }

    public V get(K key) {
        Node<K, V> node = getNode(key);
        return node == null ? null : node.value;
    }

    public V remove(K key) {
        int hash = hashKey(key);
        int bucketIndex = getHashBucketIndex(hash);

        Node<K, V> prev = null;
        Node<K, V> node = buckets[bucketIndex];
        while (node != null) {
            if (node.hash == hash && Objects.equals(node.key, key)) {
                if (prev == null) {
                    buckets[bucketIndex] = node.getNext();
                } else {
                    prev.setNext(node.getNext());
                }

                size--;
                return node.value;
            }

            prev = node;
            node = node.getNext();
        }
        return null;
    }

    public boolean containsKey(K key) {
        var node = getNode(key);
        return node != null;
    }

    public int getSize() {
        return this.size;
    }

    public int getCapacity() {
        return this.buckets.length;
    }

    public int getThreshold() {
        return (int) Math.floor(loadFactor * getCapacity());
    }

    public boolean isEmpty() {
        return this.size == 0;
    }

    public List<K> getKeys() {
        List<K> keys = new ArrayList<>(getSize());
        for (Node<K, V> head : buckets) {
            Node<K, V> node = head;
            while (node != null) {
                keys.add(node.key);
                node = node.getNext();
            }
        }

        return keys;
    }
}
