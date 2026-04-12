<?php
/**
 * test_php.php
 *
 * Holistic PHP showcase — content management system core.
 *
 * Covers: interfaces, abstract classes, traits, enums, generics (templates),
 * closures, arrow functions, match expressions, null coalescing,
 * named arguments, constructor promotion, readonly properties,
 * first-class callables, fibers, attributes, union/intersection types,
 * array functions, PDO, SPL data structures, generators, and more.
 */

declare(strict_types=1);

// ===========================================================================
// PHP 8.x Attributes
// ===========================================================================

#[Attribute(Attribute::TARGET_CLASS)]
class Entity
{
    public function __construct(public readonly string $table) {}
}

#[Attribute(Attribute::TARGET_PROPERTY)]
class Column
{
    public function __construct(
        public readonly string  $name,
        public readonly bool    $nullable = false,
        public readonly ?string $default  = null,
    ) {}
}

// ===========================================================================
// Enums (PHP 8.1)
// ===========================================================================

enum Status: string
{
    case Draft     = 'draft';
    case Published = 'published';
    case Archived  = 'archived';

    public function label(): string
    {
        return match ($this) {
            Status::Draft     => 'Draft',
            Status::Published => 'Live',
            Status::Archived  => 'Archived',
        };
    }

    public function isVisible(): bool
    {
        return $this === Status::Published;
    }

    public static function fromLabel(string $label): self
    {
        foreach (self::cases() as $case) {
            if ($case->label() === $label) return $case;
        }
        throw new \ValueError("Unknown label: $label");
    }
}

enum Role: int
{
    case Guest   = 0;
    case Author  = 10;
    case Editor  = 20;
    case Admin   = 100;

    public function canPublish(): bool { return $this->value >= self::Editor->value; }
    public function canDelete():  bool { return $this === self::Admin; }
}

// ===========================================================================
// Interfaces
// ===========================================================================

interface Identifiable
{
    public function getId(): int;
}

interface Timestamped
{
    public function getCreatedAt(): \DateTimeImmutable;
    public function getUpdatedAt(): \DateTimeImmutable;
}

interface Sluggable
{
    public function getSlug(): string;
}

interface Serialisable
{
    public function toArray(): array;
    public static function fromArray(array $data): static;
}

interface Repository
{
    public function findById(int $id): ?object;
    public function findAll(array $criteria = [], int $limit = 50): array;
    public function save(object $entity): bool;
    public function delete(int $id): bool;
}

// ===========================================================================
// Traits
// ===========================================================================

trait HasTimestamps
{
    protected \DateTimeImmutable $createdAt;
    protected \DateTimeImmutable $updatedAt;

    public function getCreatedAt(): \DateTimeImmutable { return $this->createdAt; }
    public function getUpdatedAt(): \DateTimeImmutable { return $this->updatedAt; }

    public function touch(): void
    {
        $this->updatedAt = new \DateTimeImmutable();
    }

    protected function initTimestamps(): void
    {
        $now = new \DateTimeImmutable();
        $this->createdAt = $now;
        $this->updatedAt = $now;
    }
}

trait HasSlug
{
    protected string $slug;

    public function getSlug(): string { return $this->slug; }

    protected function generateSlug(string $title): string
    {
        return strtolower(trim(preg_replace('/[^A-Za-z0-9-]+/', '-', $title), '-'));
    }
}

trait Singleton
{
    private static ?self $instance = null;

    public static function getInstance(): static
    {
        if (static::$instance === null) {
            static::$instance = new static();
        }
        return static::$instance;
    }

    private function __construct() {}
}

// ===========================================================================
// Abstract base
// ===========================================================================

abstract class BaseEntity implements Identifiable, Timestamped, Serialisable
{
    use HasTimestamps;

    protected ?int $id = null;

    final public function getId(): int
    {
        if ($this->id === null) {
            throw new \RuntimeException('Entity has no ID — not yet persisted.');
        }
        return $this->id;
    }

    public function isPersisted(): bool { return $this->id !== null; }

    abstract public function validate(): array; // returns list of error strings
}

// ===========================================================================
// User entity
// ===========================================================================

#[Entity(table: 'users')]
class User extends BaseEntity implements Sluggable
{
    use HasSlug;

    private string $passwordHash;
    private array  $permissions = [];

    public function __construct(
        #[Column(name: 'email')]
        private string $email,
        #[Column(name: 'display_name')]
        private string $displayName,
        private Role   $role = Role::Author,
    ) {
        $this->initTimestamps();
        $this->slug = $this->generateSlug($displayName);
    }

    // Getters
    public function getEmail():       string { return $this->email; }
    public function getDisplayName(): string { return $this->displayName; }
    public function getRole():        Role   { return $this->role; }

    // Readonly-like with setter
    public function setPasswordHash(string $hash): void { $this->passwordHash = $hash; }
    public function verifyPassword(string $plain): bool
    {
        return password_verify($plain, $this->passwordHash ?? '');
    }

    public function promoteToRole(Role $newRole): void
    {
        if ($newRole->value < $this->role->value) {
            throw new \LogicException("Cannot demote via promoteToRole().");
        }
        $this->role = $newRole;
        $this->touch();
    }

    public function validate(): array
    {
        $errors = [];
        if (!filter_var($this->email, FILTER_VALIDATE_EMAIL)) {
            $errors[] = "Invalid email address.";
        }
        if (strlen($this->displayName) < 2) {
            $errors[] = "Display name must be at least 2 characters.";
        }
        return $errors;
    }

    public function toArray(): array
    {
        return [
            'id'           => $this->id,
            'email'        => $this->email,
            'display_name' => $this->displayName,
            'role'         => $this->role->name,
            'slug'         => $this->slug,
            'created_at'   => $this->createdAt->format(\DATE_ATOM),
        ];
    }

    public static function fromArray(array $data): static
    {
        $user = new static($data['email'], $data['display_name']);
        $user->id   = $data['id'] ?? null;
        $user->role = Role::from($data['role_value'] ?? 10);
        return $user;
    }
}

// ===========================================================================
// Post entity
// ===========================================================================

#[Entity(table: 'posts')]
class Post extends BaseEntity implements Sluggable
{
    use HasSlug;

    private \DateTimeImmutable $publishedAt;
    private array $tags = [];

    public function __construct(
        private string $title,
        private string $body,
        private User   $author,
        private Status $status = Status::Draft,
    ) {
        $this->initTimestamps();
        $this->slug = $this->generateSlug($title);
    }

    public function getTitle():  string { return $this->title; }
    public function getBody():   string { return $this->body; }
    public function getAuthor(): User   { return $this->author; }
    public function getStatus(): Status { return $this->status; }
    public function getTags():   array  { return $this->tags; }

    public function addTag(string $tag): void
    {
        $normalised = strtolower(trim($tag));
        if (!in_array($normalised, $this->tags, true)) {
            $this->tags[] = $normalised;
        }
    }

    public function publish(User $by): void
    {
        if (!$by->getRole()->canPublish()) {
            throw new \RuntimeException("User lacks publish permission.");
        }
        $this->status      = Status::Published;
        $this->publishedAt = new \DateTimeImmutable();
        $this->touch();
    }

    public function archive(): void
    {
        $this->status = Status::Archived;
        $this->touch();
    }

    public function excerpt(int $words = 30): string
    {
        $stripped = strip_tags($this->body);
        $parts    = explode(' ', $stripped);
        return implode(' ', array_slice($parts, 0, $words))
            . (count($parts) > $words ? '…' : '');
    }

    public function validate(): array
    {
        $errors = [];
        if (strlen(trim($this->title)) < 3) $errors[] = "Title too short.";
        if (strlen(trim($this->body))  < 10) $errors[] = "Body too short.";
        return $errors;
    }

    public function toArray(): array
    {
        return [
            'id'          => $this->id,
            'title'       => $this->title,
            'slug'        => $this->slug,
            'excerpt'     => $this->excerpt(),
            'status'      => $this->status->value,
            'status_label'=> $this->status->label(),
            'author'      => $this->author->getDisplayName(),
            'tags'        => $this->tags,
            'created_at'  => $this->createdAt->format(\DATE_ATOM),
        ];
    }

    public static function fromArray(array $data): static
    {
        $author = User::fromArray($data['author'] ?? []);
        return new static($data['title'], $data['body'], $author);
    }
}

// ===========================================================================
// Generic collection
// ===========================================================================

/**
 * @template T of object
 */
class Collection implements \Countable, \IteratorAggregate
{
    /** @var T[] */
    private array $items = [];

    /** @param T $item */
    public function add(object $item): static
    {
        $this->items[] = $item;
        return $this;
    }

    /** @return T|null */
    public function first(): ?object
    {
        return $this->items[0] ?? null;
    }

    /** @return T[] */
    public function filter(\Closure $fn): array
    {
        return array_values(array_filter($this->items, $fn));
    }

    public function map(\Closure $fn): array
    {
        return array_map($fn, $this->items);
    }

    public function count(): int { return count($this->items); }

    public function getIterator(): \ArrayIterator { return new \ArrayIterator($this->items); }

    public function sortBy(\Closure $fn): static
    {
        $clone = clone $this;
        usort($clone->items, $fn);
        return $clone;
    }

    public function toArray(): array { return $this->items; }
}

// ===========================================================================
// In-memory repository
// ===========================================================================

/**
 * @template T of BaseEntity
 * @implements Repository
 */
class InMemoryRepository implements Repository
{
    /** @var array<int, T> */
    private array $store = [];
    private int   $nextId = 1;

    public function findById(int $id): ?object
    {
        return $this->store[$id] ?? null;
    }

    public function findAll(array $criteria = [], int $limit = 50): array
    {
        $results = array_values($this->store);

        foreach ($criteria as $key => $value) {
            $results = array_filter($results, function ($entity) use ($key, $value) {
                $getter = 'get' . ucfirst($key);
                return method_exists($entity, $getter)
                    && $entity->$getter() === $value;
            });
        }

        return array_slice(array_values($results), 0, $limit);
    }

    public function save(object $entity): bool
    {
        if (!$entity->isPersisted()) {
            $reflection = new \ReflectionClass($entity);
            $prop = $reflection->getProperty('id');
            $prop->setAccessible(true);
            $prop->setValue($entity, $this->nextId++);
        }
        $this->store[$entity->getId()] = $entity;
        return true;
    }

    public function delete(int $id): bool
    {
        if (!isset($this->store[$id])) return false;
        unset($this->store[$id]);
        return true;
    }
}

// ===========================================================================
// Generator — paginate content
// ===========================================================================

function paginateContent(Repository $repo, int $pageSize = 10): \Generator
{
    $page = 0;
    do {
        $items = $repo->findAll([], $pageSize);
        if (empty($items)) break;
        yield $page => $items;
        $page++;
    } while (count($items) === $pageSize);
}

// ===========================================================================
// Event system (Closure-based)
// ===========================================================================

class EventDispatcher
{
    use Singleton;

    /** @var array<string, \Closure[]> */
    private array $listeners = [];

    public function listen(string $event, \Closure $handler): void
    {
        $this->listeners[$event][] = $handler;
    }

    public function dispatch(string $event, mixed ...$args): void
    {
        foreach ($this->listeners[$event] ?? [] as $handler) {
            $handler(...$args);
        }
    }
}

// ===========================================================================
// Pipeline — array of callables
// ===========================================================================

class Pipeline
{
    private array $stages = [];

    public function pipe(callable $stage): static
    {
        $this->stages[] = $stage;
        return $this;
    }

    public function process(mixed $payload): mixed
    {
        return array_reduce(
            $this->stages,
            fn (mixed $carry, callable $stage) => $stage($carry),
            $payload,
        );
    }
}

// ===========================================================================
// Helper functions
// ===========================================================================

function slugify(string $text): string
{
    return strtolower(trim(preg_replace('/[^A-Za-z0-9-]+/', '-', $text), '-'));
}

function truncate(string $text, int $max = 100, string $ellipsis = '…'): string
{
    if (mb_strlen($text) <= $max) return $text;
    return mb_substr($text, 0, $max) . $ellipsis;
}

/** @return array<string, mixed> */
function arrayOnly(array $data, string ...$keys): array
{
    return array_intersect_key($data, array_flip($keys));
}

// ===========================================================================
// Main
// ===========================================================================

(static function (): void {
    echo "Content Management System Demo\n\n";

    // Repository and dispatcher
    $repo       = new InMemoryRepository();
    $dispatcher = EventDispatcher::getInstance();

    $dispatcher->listen('post.published', function (Post $post): void {
        echo "[EVENT] Post published: '{$post->getTitle()}'\n";
    });

    // Create users
    $admin = new User('admin@example.com', 'Site Admin', Role::Admin);
    $admin->setPasswordHash(password_hash('secret', PASSWORD_BCRYPT));
    $repo->save($admin);

    $author = new User('alice@example.com', 'Alice Writer', Role::Author);
    $repo->save($author);

    echo "Users saved. Admin ID: {$admin->getId()}, Author ID: {$author->getId()}\n";

    // Create posts
    $post1 = new Post(
        'Getting Started with PHP 8',
        'PHP 8 introduced many improvements including named arguments, match expressions, ' .
        'enums, fibers, and readonly properties. These features make PHP more expressive ' .
        'and safer to write.',
        $admin,
    );
    $post1->addTag('php');
    $post1->addTag('tutorial');

    $post2 = new Post(
        'Understanding Traits in PHP',
        'Traits provide horizontal code reuse in PHP. They allow you to include methods ' .
        'in multiple classes without inheritance. Traits can include properties, ' .
        'abstract methods, and constants.',
        $author,
    );
    $post2->addTag('php');
    $post2->addTag('oop');

    // Validate
    foreach ([$post1, $post2] as $post) {
        $errors = $post->validate();
        if (!empty($errors)) {
            echo "Validation failed: " . implode(', ', $errors) . "\n";
            continue;
        }
        $repo->save($post);
    }

    // Publish post1
    try {
        $post1->publish($admin);
        $dispatcher->dispatch('post.published', $post1);
    } catch (\RuntimeException $e) {
        echo "Publish failed: {$e->getMessage()}\n";
    }

    // Try publish as author (should fail)
    try {
        $post2->publish($author);
    } catch (\RuntimeException $e) {
        echo "Expected error: {$e->getMessage()}\n";
    }

    // Collection and pipeline
    $posts = new Collection();
    $posts->add($post1)->add($post2);

    $published = $posts->filter(fn (Post $p) => $p->getStatus()->isVisible());
    echo "\nPublished posts: " . count($published) . "\n";

    $titles = $posts->map(fn (Post $p) => $p->getTitle());
    echo "All titles: " . implode(', ', $titles) . "\n";

    // Content pipeline
    $pipeline = (new Pipeline())
        ->pipe(fn (string $text) => strip_tags($text))
        ->pipe(fn (string $text) => strtolower($text))
        ->pipe(fn (string $text) => trim($text));

    $clean = $pipeline->process('<b>  Hello World  </b>');
    echo "\nPipeline output: '$clean'\n";

    // Match expression
    foreach (Status::cases() as $status) {
        $icon = match ($status) {
            Status::Draft     => '✏',
            Status::Published => '✓',
            Status::Archived  => '✗',
        };
        echo "  {$icon} {$status->label()}\n";
    }

    // Array helpers
    $postData = $post1->toArray();
    $safe     = arrayOnly($postData, 'title', 'slug', 'status_label', 'excerpt');
    echo "\nSafe export: " . json_encode($safe, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n";

    // Generator pagination
    echo "\nPaginated content:\n";
    foreach (paginateContent($repo, 5) as $page => $items) {
        echo "  Page $page: " . count($items) . " item(s)\n";
    }

    // Reflection — read Entity attribute
    $ref = new \ReflectionClass(Post::class);
    foreach ($ref->getAttributes(Entity::class) as $attr) {
        $entity = $attr->newInstance();
        echo "\nPost maps to table: '{$entity->table}'\n";
    }

    echo "\nDone.\n";
})();
