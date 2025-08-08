# 18. Messaging

## Overview

Messaging is how one part of a system tells another part "something happened" without calling it directly. There are two big flavors. In-process events stay inside a single JVM — one object publishes an event, another object listens for it, and Spring wires them together. A real message broker (Kafka, RabbitMQ, JMS/Artemis) lives outside your application and can pass messages between different services, different servers, even different companies. In-process events are simpler and faster but disappear if the app crashes before a listener runs. A broker gives you durability, decoupling between services, and the ability to scale consumers independently, at the cost of more moving parts and trickier failure modes. This chapter walks through both worlds, plus the patterns (retries, dead letter queues, outbox) that make messaging reliable in production.

## Spring Events

Spring has a built-in, in-process publish/subscribe mechanism called the **ApplicationEvent** system. It lives entirely inside one Spring `ApplicationContext` — no network call, no broker, just Java method calls under the hood. It is useful for decoupling code inside a single application, for example: "when an order is placed, send a confirmation email" without the order-placing code needing to know anything about emails.

Key building blocks:

- `ApplicationEventPublisher` — the interface you inject to publish events.
- An event object — any plain Java object works since Spring 4.2. You no longer need to extend `ApplicationEvent`.
- `@EventListener` — an annotation you put on a method to receive events of a specific type.

```java
// The event - a plain POJO, no base class required
public class OrderPlacedEvent {

    private final String orderId;
    private final String customerEmail;

    public OrderPlacedEvent(String orderId, String customerEmail) {
        this.orderId = orderId;
        this.customerEmail = customerEmail;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getCustomerEmail() {
        return customerEmail;
    }
}
```

```java
@Service
public class OrderService {

    private final ApplicationEventPublisher publisher;

    public OrderService(ApplicationEventPublisher publisher) {
        this.publisher = publisher;
    }

    public void placeOrder(Order order) {
        // ... save the order to the database ...
        publisher.publishEvent(new OrderPlacedEvent(order.getId(), order.getCustomerEmail()));
    }
}
```

```java
@Component
public class OrderEmailListener {

    @EventListener
    public void onOrderPlaced(OrderPlacedEvent event) {
        // send a confirmation email
        System.out.println("Sending email for order " + event.getOrderId());
    }
}
```

By default, `publishEvent` calls listeners **synchronously**, on the same thread, in the order they are discovered (which is not guaranteed unless you control it explicitly). If a listener throws an exception, it propagates back to the publisher — so a broken listener can break the original method call.

### Ordering listeners

If multiple listeners react to the same event and order matters, use `@Order`:

```java
@Component
public class AuditListener {

    @Order(1)
    @EventListener
    public void onOrderPlaced(OrderPlacedEvent event) {
        System.out.println("Audit log entry for " + event.getOrderId());
    }
}
```

### Making listeners asynchronous

If a listener does slow work (calling an external API, sending an email), running it synchronously blocks the publisher's thread — which might be an HTTP request thread. Use `@Async` to run the listener on a separate thread pool:

```java
@Configuration
@EnableAsync
public class AsyncConfig {
}
```

```java
@Component
public class OrderEmailListener {

    @Async
    @EventListener
    public void onOrderPlaced(OrderPlacedEvent event) {
        // now runs on a separate thread; exceptions no longer propagate to the caller
        sendConfirmationEmail(event);
    }
}
```

Careful: with `@Async`, exceptions thrown by the listener are logged (or handled by an `AsyncUncaughtExceptionHandler`) but never bubble back to the code that published the event. Silent failures are easy to miss.

## Application Events

"Application events" here means Spring's own **lifecycle events** — events the framework publishes about itself, plus the transactional flavor of `@EventListener` you'll use constantly in real apps.

### Built-in lifecycle events

| Event | When it fires |
|---|---|
| `ContextRefreshedEvent` | The `ApplicationContext` has been initialized or refreshed (all beans loaded). |
| `ContextStartedEvent` | The context has been started via `start()`. |
| `ContextStoppedEvent` | The context has been stopped via `stop()`. |
| `ContextClosedEvent` | The context is closing, beans are being destroyed. |
| `ApplicationReadyEvent` | The Spring Boot application is fully started and ready to serve requests. |
| `ApplicationFailedEvent` | Startup failed. |

```java
@Component
public class StartupBanner {

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        System.out.println("Application is ready to accept traffic!");
    }
}
```

`ApplicationReadyEvent` is the right place to run "warm-up" logic — pre-loading a cache, checking connectivity to downstream systems — because unlike `ContextRefreshedEvent` it fires only after the whole application, including embedded servers, is fully started.

### `@TransactionalEventListener`

A very common bug: you publish an event from inside a `@Transactional` method, a listener reacts to it immediately (e.g., calls a payment API), and then the surrounding transaction rolls back. Now the payment happened for an order that was never actually saved.

`@TransactionalEventListener` fixes this by binding the listener's execution to the transaction lifecycle instead of to the moment `publishEvent` is called.

```java
@Component
public class PaymentListener {

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onOrderPlaced(OrderPlacedEvent event) {
        // only runs if the transaction that published the event actually committed
        chargeCard(event.getOrderId());
    }
}
```

Available phases:

| Phase | Runs when |
|---|---|
| `BEFORE_COMMIT` | Just before the transaction commits. |
| `AFTER_COMMIT` (default) | After the transaction has successfully committed. |
| `AFTER_ROLLBACK` | After the transaction has rolled back. |
| `AFTER_COMPLETION` | After commit or rollback, either way. |

If there is no active transaction when the event is published, by default the listener is **not called at all** unless you set `fallbackExecution = true`.

```java
@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT, fallbackExecution = true)
public void onOrderPlaced(OrderPlacedEvent event) {
    chargeCard(event.getOrderId());
}
```

`AFTER_COMMIT` is the safest default for side effects that talk to the outside world (emails, payments, calling other services) because it guarantees the data you're reacting to was actually persisted.

## Kafka

Apache Kafka is a distributed, durable **log** of messages, organized into **topics**. Producers append messages ("records") to a topic; consumers read them, tracking their own position (**offset**) in the log. Kafka does not delete a message once it's been read — it keeps messages for a configured retention period, so multiple independent consumer groups can each read the same topic at their own pace.

Spring Boot integrates with Kafka via the `spring-kafka` library.

```xml
<dependency>
    <groupId>org.springframework.kafka</groupId>
    <artifactId>spring-kafka</artifactId>
</dependency>
```

```yaml
spring:
  kafka:
    bootstrap-servers: localhost:9092
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: org.springframework.kafka.support.serializer.JsonSerializer
    consumer:
      group-id: order-service
      auto-offset-reset: earliest
      key-deserializer: org.apache.kafka.common.serialization.StringDeserializer
      value-deserializer: org.springframework.kafka.support.serializer.JsonDeserializer
      properties:
        spring.json.trusted.packages: "com.example.orders.events"
```

### Producing with `KafkaTemplate`

```java
@Service
public class OrderEventProducer {

    private final KafkaTemplate<String, OrderPlacedEvent> kafkaTemplate;

    public OrderEventProducer(KafkaTemplate<String, OrderPlacedEvent> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publish(OrderPlacedEvent event) {
        // key = orderId ensures all events for the same order land on the same partition,
        // which preserves ordering for that order
        kafkaTemplate.send("orders", event.getOrderId(), event);
    }
}
```

### Consuming with `@KafkaListener`

```java
@Component
public class OrderEventConsumer {

    @KafkaListener(topics = "orders", groupId = "order-service")
    public void onMessage(OrderPlacedEvent event) {
        System.out.println("Received order " + event.getOrderId());
    }
}
```

### Consumer groups, partitions, keys, and ordering

- A **topic** is split into **partitions**. Each partition is an ordered log.
- Kafka guarantees ordering **only within a partition**, not across the whole topic.
- Messages with the same **key** always go to the same partition (as long as the partition count doesn't change), which is how you get ordering "per entity" — e.g., all events for `orderId=123` in order.
- A **consumer group** is a set of consumer instances that split the partitions between them — each partition is read by exactly one consumer in the group at a time. More consumers (up to the partition count) means more parallelism.

| Concept | Purpose |
|---|---|
| Topic | Named stream of records. |
| Partition | Ordered subdivision of a topic; unit of parallelism. |
| Key | Determines which partition a record goes to. |
| Consumer group | Set of consumers sharing the work of reading a topic. |
| Offset | A consumer's position (last read record) within a partition. |

### Offsets and `AckMode`

Kafka does not delete or "pop" messages — a consumer just tracks how far it has read via a committed offset. Where you commit that offset relative to processing the message determines your delivery guarantee.

```java
@Bean
public ConcurrentKafkaListenerContainerFactory<String, OrderPlacedEvent> kafkaListenerContainerFactory(
        ConsumerFactory<String, OrderPlacedEvent> consumerFactory) {

    ConcurrentKafkaListenerContainerFactory<String, OrderPlacedEvent> factory =
            new ConcurrentKafkaListenerContainerFactory<>();
    factory.setConsumerFactory(consumerFactory);
    // MANUAL means the listener code decides exactly when to acknowledge
    factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL);
    return factory;
}
```

```java
@KafkaListener(topics = "orders", groupId = "order-service")
public void onMessage(OrderPlacedEvent event, Acknowledgment ack) {
    process(event);
    ack.acknowledge(); // only commit the offset after processing succeeds
}
```

| `AckMode` | Behavior |
|---|---|
| `RECORD` | Commit offset after each record is processed. |
| `BATCH` (default) | Commit offset after each batch (poll) is processed. |
| `MANUAL` | Listener calls `ack.acknowledge()` explicitly, commit happens on next poll. |
| `MANUAL_IMMEDIATE` | Same as `MANUAL` but commits immediately, synchronously. |

### At-least-once vs exactly-once, and idempotent consumers

- **At-least-once** (the common default): a message might be delivered more than once, for example if the consumer crashes after processing but before committing the offset. Your consumer must tolerate duplicates.
- **Exactly-once**: harder to achieve end-to-end; Kafka supports transactional producers/consumers to get exactly-once *within Kafka*, but as soon as you write to an external database or call another API, you're back to needing your own deduplication.
- An **idempotent consumer** is one where processing the same message twice has the same effect as processing it once — e.g., using the message's unique ID to check "have I already applied this?" before acting.

```java
@KafkaListener(topics = "orders", groupId = "order-service")
public void onMessage(OrderPlacedEvent event, Acknowledgment ack) {
    if (processedOrderRepository.existsById(event.getOrderId())) {
        ack.acknowledge(); // already handled, skip safely
        return;
    }
    handleOrder(event);
    processedOrderRepository.save(new ProcessedOrder(event.getOrderId()));
    ack.acknowledge();
}
```

## RabbitMQ

RabbitMQ is a **message broker** built around the AMQP protocol. Unlike Kafka's "durable log you replay," RabbitMQ is closer to a traditional queueing system: a producer sends a message to an **exchange**, the exchange routes it to one or more **queues** based on **bindings** and a **routing key**, and consumers pull messages off queues. Once a message is acknowledged, it's normally removed from the queue.

Spring Boot integrates with RabbitMQ via `spring-amqp` / `spring-boot-starter-amqp`.

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-amqp</artifactId>
</dependency>
```

### Exchanges, queues, bindings, routing keys

| Exchange type | Routing behavior |
|---|---|
| `direct` | Routes to queues whose binding key exactly matches the message's routing key. |
| `topic` | Routes using wildcard patterns (`*` one word, `#` many words) against the routing key, e.g. `orders.*.created`. |
| `fanout` | Ignores the routing key; broadcasts to every bound queue. |
| `headers` | Routes based on message header values instead of the routing key. |

```java
@Configuration
public class RabbitConfig {

    @Bean
    public TopicExchange orderExchange() {
        return new TopicExchange("orders.exchange");
    }

    @Bean
    public Queue orderQueue() {
        return new Queue("orders.queue", true); // durable = survives broker restart
    }

    @Bean
    public Binding binding(Queue orderQueue, TopicExchange orderExchange) {
        return BindingBuilder.bind(orderQueue).to(orderExchange).with("orders.created");
    }

    @Bean
    public Jackson2JsonMessageConverter jsonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }
}
```

### Publishing with `RabbitTemplate`

```java
@Service
public class OrderEventPublisher {

    private final RabbitTemplate rabbitTemplate;

    public OrderEventPublisher(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publish(OrderPlacedEvent event) {
        rabbitTemplate.convertAndSend("orders.exchange", "orders.created", event);
    }
}
```

### Consuming with `@RabbitListener`

```java
@Component
public class OrderEventListener {

    @RabbitListener(queues = "orders.queue")
    public void onMessage(OrderPlacedEvent event, Channel channel,
                           @Header(AmqpHeaders.DELIVERY_TAG) long tag) throws IOException {
        try {
            process(event);
            channel.basicAck(tag, false); // manual acknowledgment
        } catch (Exception ex) {
            channel.basicNack(tag, false, false); // don't requeue - send to DLQ instead
        }
    }
}
```

To use manual acks, set the listener container's ack mode:

```yaml
spring:
  rabbitmq:
    listener:
      simple:
        acknowledge-mode: manual
```

### Publisher confirms

By default, `rabbitTemplate.convertAndSend` fires and forgets — it doesn't know if the broker actually accepted the message. **Publisher confirms** let RabbitMQ tell your app "yes, I have it (or persisted it)," so you can detect and retry failed publishes.

```yaml
spring:
  rabbitmq:
    publisher-confirm-type: correlated
    publisher-returns: true
```

```java
rabbitTemplate.setConfirmCallback((correlationData, ack, cause) -> {
    if (!ack) {
        System.out.println("Message was NOT confirmed by broker: " + cause);
    }
});
```

## JMS

JMS (Java Message Service) is a Java standard API for messaging — not a broker itself, but an interface that brokers implement. **ActiveMQ Artemis** is the most common modern JMS broker used with Spring Boot (it replaced the older "ActiveMQ Classic" as Spring Boot's default). Because JMS is a standard, `JmsTemplate` and `@JmsListener` code looks similar no matter which JMS broker sits behind it.

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-artemis</artifactId>
</dependency>
```

```yaml
spring:
  artemis:
    mode: embedded  # or 'native' to connect to an external broker
```

### Queues vs topics

| | Queue | Topic |
|---|---|---|
| Delivery model | Point-to-point: one message goes to exactly one consumer | Publish/subscribe: one message goes to every subscriber |
| Use case | Work distribution (only one worker should handle each task) | Broadcasting (many parts of the system all need to know) |

### Sending with `JmsTemplate`

```java
@Service
public class NotificationSender {

    private final JmsTemplate jmsTemplate;

    public NotificationSender(JmsTemplate jmsTemplate) {
        this.jmsTemplate = jmsTemplate;
    }

    public void sendToQueue(String orderId) {
        jmsTemplate.convertAndSend("order.notifications", orderId);
    }
}
```

### Receiving with `@JmsListener`

```java
@Component
public class NotificationListener {

    @JmsListener(destination = "order.notifications")
    public void onMessage(String orderId) {
        System.out.println("Notify about order " + orderId);
    }
}
```

JMS is common in enterprise environments (banking, telecom) that already standardized on it, or when you need a broker that supports transactions and strict message ordering guarantees within a queue without adopting Kafka's log-based model.

## Dead Letter Queues

A **Dead Letter Queue (DLQ)** is a separate queue/topic where "poison" messages go — messages that repeatedly fail processing. Without a DLQ, a broken message can get retried forever, blocking everything behind it (in Kafka, blocking the whole partition; in RabbitMQ, potentially looping the same message).

### Kafka: `DefaultErrorHandler` + `DeadLetterPublishingRecoverer`

```java
@Bean
public DefaultErrorHandler errorHandler(KafkaTemplate<Object, Object> kafkaTemplate) {
    DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(kafkaTemplate,
            (record, ex) -> new TopicPartition(record.topic() + ".DLT", record.partition()));

    DefaultErrorHandler errorHandler = new DefaultErrorHandler(recoverer, new FixedBackOff(1000L, 3));
    return errorHandler;
}
```

```java
@Bean
public ConcurrentKafkaListenerContainerFactory<String, OrderPlacedEvent> kafkaListenerContainerFactory(
        ConsumerFactory<String, OrderPlacedEvent> consumerFactory,
        DefaultErrorHandler errorHandler) {

    ConcurrentKafkaListenerContainerFactory<String, OrderPlacedEvent> factory =
            new ConcurrentKafkaListenerContainerFactory<>();
    factory.setConsumerFactory(consumerFactory);
    factory.setCommonErrorHandler(errorHandler);
    return factory;
}
```

After 3 failed attempts (per the `FixedBackOff`), the record is published to `orders.DLT` and the consumer's offset moves on — so one bad message doesn't block the rest of the partition forever.

### RabbitMQ: `x-dead-letter-exchange`

```java
@Bean
public Queue orderQueue() {
    return QueueBuilder.durable("orders.queue")
            .withArgument("x-dead-letter-exchange", "orders.dlx")
            .withArgument("x-dead-letter-routing-key", "orders.dead")
            .build();
}

@Bean
public DirectExchange deadLetterExchange() {
    return new DirectExchange("orders.dlx");
}

@Bean
public Queue deadLetterQueue() {
    return new Queue("orders.queue.dlq", true);
}

@Bean
public Binding dlqBinding(Queue deadLetterQueue, DirectExchange deadLetterExchange) {
    return BindingBuilder.bind(deadLetterQueue).to(deadLetterExchange).with("orders.dead");
}
```

When a consumer `nack`s a message with `requeue=false` (or the message expires, or the queue is full), RabbitMQ routes it to the configured dead letter exchange instead of discarding it.

### What to do with DLQ messages

- Alert a human or an on-call channel — a growing DLQ usually means a bug or a bad upstream message.
- Inspect the message and the failure reason (log it alongside the message when dead-lettering).
- Fix the root cause, then **replay** the message back into the original topic/queue once it's safe.
- Never let a DLQ silently grow forever — treat it as an operational queue that needs owners and monitoring.

## Retry

Transient failures — a downstream service being briefly unavailable, a database connection hiccup — are common. Retrying with a delay often resolves them without any human intervention. The key design question is: does the retry block the consumer (**blocking retry**), or does it get rescheduled for later while other messages keep flowing (**non-blocking retry**)?

### Kafka: `DefaultErrorHandler` with `ExponentialBackOff`

```java
@Bean
public DefaultErrorHandler errorHandler(KafkaTemplate<Object, Object> kafkaTemplate) {
    ExponentialBackOff backOff = new ExponentialBackOff(1000L, 2.0);
    backOff.setMaxInterval(30000L);
    backOff.setMaxElapsedTime(120000L);

    DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(kafkaTemplate);
    return new DefaultErrorHandler(recoverer, backOff);
}
```

This is **blocking** by default: the consumer thread pauses and retries the same record in place before moving on, which means it also blocks everything else waiting behind it on that partition.

### `@RetryableTopic` — non-blocking retry for Kafka

`@RetryableTopic` avoids blocking by publishing the failed message to a separate "retry" topic with a delay, instead of pausing the consumer.

```java
@RetryableTopic(
        attempts = "4",
        backoff = @Backoff(delay = 1000, multiplier = 2.0),
        dltStrategy = DltStrategy.FAIL_ON_ERROR
)
@KafkaListener(topics = "orders", groupId = "order-service")
public void onMessage(OrderPlacedEvent event) {
    process(event);
}
```

Spring Kafka automatically creates topics like `orders-retry-0`, `orders-retry-1`, and finally `orders-dlt`, and moves failing messages through them, freeing up the original partition to keep processing new messages.

### `spring-retry`: `RetryTemplate` and `@Retryable`

For non-Kafka code (or any regular method call — a REST client call, a database call), `spring-retry` gives you generic retry support.

```xml
<dependency>
    <groupId>org.springframework.retry</groupId>
    <artifactId>spring-retry</artifactId>
</dependency>
```

```java
@Configuration
@EnableRetry
public class RetryConfig {
}
```

```java
@Service
public class PaymentClient {

    @Retryable(
            retryFor = {TransientPaymentException.class},
            maxAttempts = 4,
            backoff = @Backoff(delay = 500, multiplier = 2.0)
    )
    public void charge(String orderId) {
        // calls an external payment API
    }

    @Recover
    public void recover(TransientPaymentException ex, String orderId) {
        // called after all retries are exhausted
        System.out.println("Giving up on payment for " + orderId);
    }
}
```

Or imperatively with `RetryTemplate`, useful when you need retry logic outside of a Spring-managed bean method:

```java
RetryTemplate retryTemplate = RetryTemplate.builder()
        .maxAttempts(3)
        .exponentialBackoff(500, 2.0, 5000)
        .retryOn(TransientPaymentException.class)
        .build();

retryTemplate.execute(context -> {
    paymentClient.charge(orderId);
    return null;
});
```

| Approach | Blocking? | Typical use |
|---|---|---|
| `DefaultErrorHandler` + `FixedBackOff`/`ExponentialBackOff` | Blocking (pauses the partition) | Simple Kafka setups, short retries only |
| `@RetryableTopic` | Non-blocking (uses separate retry topics) | Kafka consumers that shouldn't stall the partition |
| `@Retryable` / `RetryTemplate` | Blocking (pauses the calling thread) | Any method call, e.g. calling a flaky REST API |

## Event-Driven Architecture

Event-Driven Architecture (EDA) is a style of designing systems around emitting and reacting to events rather than direct service-to-service calls. It naturally fits with brokers like Kafka and RabbitMQ.

### Choreography vs orchestration

- **Choreography**: each service reacts to events and emits new events; there's no central coordinator. `OrderService` emits `OrderPlaced`, `InventoryService` reacts and emits `StockReserved`, `ShippingService` reacts to that. Simple to add participants, but harder to see the overall flow ("who's actually driving this?").
- **Orchestration**: a central coordinator (an "orchestrator" or saga manager) explicitly tells each service what to do next and tracks the overall process state. Easier to reason about and monitor, but the orchestrator becomes a critical, more complex component.

| | Choreography | Orchestration |
|---|---|---|
| Coordination | Implicit, via events | Explicit, via a central coordinator |
| Coupling | Loose between services | Services coupled to the orchestrator |
| Visibility of overall flow | Harder — spread across services | Easier — centralized |
| Good for | Simple, few-step flows | Complex, multi-step business processes (sagas) |

### The transactional outbox pattern

A very common bug: your code saves something to the database *and* publishes an event to a broker as two separate steps. If the app crashes between them, or the broker call fails after the database commit, the two systems disagree — this is the **dual-write problem**.

The **outbox pattern** fixes this by writing the event to an `outbox` table in the *same database transaction* as the business data. A separate process (a poller, or a change-data-capture tool like Debezium) reads the outbox table and reliably publishes the events to the broker.

```java
@Transactional
public void placeOrder(Order order) {
    orderRepository.save(order);

    OutboxMessage message = new OutboxMessage(
            "OrderPlaced", toJson(order), Instant.now());
    outboxRepository.save(message); // same transaction, same commit as the order save
}
```

```java
@Scheduled(fixedDelay = 1000)
public void publishOutboxMessages() {
    List<OutboxMessage> pending = outboxRepository.findUnpublished();
    for (OutboxMessage message : pending) {
        kafkaTemplate.send("orders", message.getPayload());
        message.markPublished();
        outboxRepository.save(message);
    }
}
```

Because the order and the outbox row commit together atomically, you'll never end up with "order saved but no event," or "event sent but order rollback."

### Idempotency keys

Because brokers generally offer at-least-once delivery, consumers will occasionally see the same message twice. An **idempotency key** — usually a unique event ID or business ID — lets a consumer recognize and skip duplicates:

```java
if (idempotencyKeyRepository.existsById(event.getEventId())) {
    return; // already processed, skip
}
handleEvent(event);
idempotencyKeyRepository.save(new IdempotencyKey(event.getEventId()));
```

### Eventual consistency

In an event-driven system, different services' data will briefly disagree after a change — `OrderService` knows about the new order immediately, but `InventoryService` only finds out a moment later when the event arrives and is processed. This is **eventual consistency**: the system converges to a consistent state, just not instantly. Design your UI and business logic to tolerate this lag (e.g., show "processing" states) rather than assuming everything is always in sync.

### Event schema versioning

Event payloads evolve over time — a new field gets added, an old one gets removed or renamed. Because producers and consumers deploy independently, you need a strategy so old consumers don't break when they see a new event shape.

- Prefer **additive** changes: add new optional fields, don't remove or repurpose old ones.
- Include a `version` field in the event payload or a schema registry (e.g., Confluent Schema Registry, Avro/Protobuf schemas) to enforce compatibility rules.
- Consider consumer-driven contract testing so producers know if a change would break an existing consumer.

```json
{
  "eventType": "OrderPlaced",
  "version": 2,
  "orderId": "abc-123",
  "customerEmail": "jane@example.com",
  "currency": "EUR"
}
```

### Comparison: Spring Events vs Kafka vs RabbitMQ vs JMS

| | Spring Events | Kafka | RabbitMQ | JMS (Artemis) |
|---|---|---|---|---|
| Scope | In-process (single JVM) | Cross-service, distributed | Cross-service, distributed | Cross-service, distributed |
| Durability | None — lost if app crashes before listener runs | Durable log with configurable retention | Durable if queue/message marked durable/persistent | Durable if configured (persistent delivery mode) |
| Delivery model | Direct method call | Pub/sub via topics + partitions | Routing via exchanges/queues | Point-to-point (queue) or pub/sub (topic) |
| Ordering | Call order (single thread) unless `@Async` | Guaranteed within a partition | Guaranteed within a single queue (mostly) | Guaranteed within a queue |
| Replay old messages | No | Yes, by resetting offsets (within retention) | No (messages removed once consumed) | Generally no |
| Typical use case | Decoupling code within one app (e.g., audit logging, side effects) | High-throughput event streaming, analytics, microservice integration | Task queues, routing, RPC-style messaging | Enterprise systems standardized on JMS |

## Common Code Review / Interview Pitfalls

- **Publishing an event inside a transaction that later rolls back.** If a plain `@EventListener` reacts immediately and does something irreversible (charges a card, sends an email), a later rollback leaves that side effect stranded. Fix: use `@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)`, or move the event into a transactional outbox so it's only ever sent once the DB change is durable.
- **Dual-write to the database and the broker without an outbox.** Saving to the DB and calling `kafkaTemplate.send(...)` as two separate, unrelated operations means a crash or network blip between them leaves the two systems out of sync. Fix: use the transactional outbox pattern so the event write is part of the same DB transaction.
- **Non-idempotent consumers.** At-least-once delivery means duplicates will happen eventually. A consumer that isn't safe to run twice (e.g., "add $10 to balance" instead of "set balance to $110") will silently corrupt data on redelivery. Fix: track processed message IDs, or make the operation naturally idempotent (upserts, "set" instead of "increment").
- **Infinite retry loops with no DLQ.** Retrying a message forever with no escape hatch means one malformed message can block a partition or clog a queue indefinitely. Fix: cap retry attempts and route exhausted messages to a dead letter queue/topic.
- **Auto-committing offsets before processing finishes.** With Kafka's default auto-commit, the offset can be committed before your business logic actually completes — if the app crashes mid-processing, that message is lost forever from that consumer's point of view. Fix: use manual acknowledgment (`AckMode.MANUAL`) and only ack after processing succeeds.
- **Assuming global ordering across Kafka partitions.** Kafka only guarantees order within a single partition, not across the whole topic. Code that assumes "message A always arrives before message B" across different keys will see reordering. Fix: key messages by the entity whose order matters (e.g., `orderId`), and only rely on ordering within that key.
- **Blocking retries stalling a whole partition.** A blocking `DefaultErrorHandler` retry pauses the consumer thread on the failing record, which blocks every other message queued behind it on that partition. Fix: use `@RetryableTopic` (or a similar non-blocking retry-topic approach) so retries happen out-of-band.
- **Sending huge messages through a broker.** Multi-megabyte payloads slow down brokers, hit size limits, and bloat retention storage. Fix: send a reference (an ID or S3/blob URL) in the message and let the consumer fetch the large payload separately.
- **No schema or versioning strategy.** Changing an event's shape without a plan breaks consumers that haven't redeployed yet, especially in a system with many independently-deployed services. Fix: make changes additive, version the schema, and consider a schema registry with compatibility checks.
- **Swallowing listener exceptions.** A `catch (Exception e) {}` around listener logic (or no error handler at all) hides failures — the message is silently "processed" even though nothing actually happened. Fix: let failures propagate to a configured error handler that can retry or dead-letter the message, and always log the failure.
- **`@EventListener` doing slow work synchronously in the request thread.** A default (non-`@Async`) listener runs on the same thread as the publisher — if that publisher is a web controller handling an HTTP request, a slow listener (calling an external API) directly slows down the response. Fix: mark the listener `@Async`, or move the slow work to a broker-backed queue instead of an in-process event.
- **Missing consumer error handler.** Without a configured `CommonErrorHandler` (Kafka) or an explicit nack strategy (RabbitMQ), an unhandled exception in a listener can crash the container, stop consumption entirely, or loop endlessly depending on defaults. Fix: always configure an explicit error handler with a bounded retry policy and a recovery/dead-letter strategy.
- **Treating exactly-once as free.** Assuming Kafka's "exactly-once" producer/transaction features magically make the entire pipeline exactly-once, including any external database writes or API calls a consumer makes. Fix: still design consumers to be idempotent; exactly-once semantics inside Kafka don't extend past Kafka's own boundary.
- **Not monitoring the DLQ.** A dead letter queue that nobody watches just becomes a silent, ever-growing pile of unprocessed failures. Fix: alert on DLQ depth and assign an owner to triage and replay dead-lettered messages.
- **Forgetting `spring.json.trusted.packages` (or equivalent) for JSON deserialization.** Spring Kafka's `JsonDeserializer` refuses to deserialize into arbitrary classes unless the package is trusted, causing confusing runtime errors. Fix: explicitly configure trusted packages (or `*` only in trusted, internal environments) and match producer/consumer type information.

## Quick Recap

- Spring's in-process events (`ApplicationEventPublisher` + `@EventListener`) decouple code within one JVM; no base class needed for the event object since Spring 4.2.
- Use `@TransactionalEventListener(phase = AFTER_COMMIT)` to avoid acting on events tied to a transaction that might roll back.
- `@Async` on a listener moves the work off the caller's thread — but then exceptions no longer propagate back to the publisher.
- `ApplicationReadyEvent` fires once the whole app (including the embedded server) is up; `ContextRefreshedEvent` fires once the Spring context itself is ready.
- Kafka is a durable, partitioned log: ordering is guaranteed only within a partition, and the message key decides the partition.
- Control offset commits with `AckMode` (prefer `MANUAL`/`MANUAL_IMMEDIATE`) so you don't lose messages on crash, and design consumers to be idempotent for at-least-once delivery.
- RabbitMQ routes messages through exchanges (direct, topic, fanout, headers) to queues via bindings and routing keys; use publisher confirms and manual acks for reliability.
- JMS is a standard API (queues = point-to-point, topics = pub/sub); ActiveMQ Artemis is the common modern broker behind it in Spring Boot.
- Dead letter queues catch messages that keep failing so they don't block everything behind them — Kafka via `DeadLetterPublishingRecoverer`, RabbitMQ via `x-dead-letter-exchange`.
- Prefer non-blocking retry (`@RetryableTopic` for Kafka) over blocking retry when you can't afford to stall a partition/queue.
- The transactional outbox pattern solves the dual-write problem by saving the event in the same DB transaction as the business data.
- Design for eventual consistency, idempotency keys, and versioned event schemas — these are what make an event-driven architecture actually reliable in production.
